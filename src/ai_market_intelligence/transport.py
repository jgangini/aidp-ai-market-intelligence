"""Minimal signed AIDP transport extracted from Starter Kits 3c82617.

Only the explicit installer/permission/files/credential method closure is retained.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote
from oci._vendor import requests
from .settings import Settings
from .errors import AidpProvisionPending, AidpProvisionError, AidpRequestError, AidpProvisionConflict
from .runtime_credentials import shared_credential
API_VERSION = "20260430"
GOVERNANCE_CREDENTIAL_NAME = "AidpRuntime"

class AidpClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base = (
            f"https://datalake.{settings.aidp_region}.oci.oraclecloud.com/{API_VERSION}/"
            f"aiDataPlatforms/{settings.aidp_platform_id}"
        )
        import oci

        self._oci = oci
        config = {**oci.config.from_file(settings.oci_config_file, "DEFAULT"), 'region': settings.aidp_region}
        self._oci_config = config
        self.signer = oci.signer.Signer(
            tenancy=config["tenancy"],
            user=config["user"],
            fingerprint=config["fingerprint"],
            private_key_file_location=config["key_file"],
            pass_phrase=config.get("pass_phrase"),
        )
        self.object_storage = oci.object_storage.ObjectStorageClient(config)
        self.session = requests.Session()
        self._session_lock = threading.Lock()
        # ponytail: process-local locks serialize one participant; use a distributed lock if the API is replicated.
        self._locks: dict[str, asyncio.Lock] = {}


    async def close(self) -> None:
        self.session.close()


    @staticmethod
    def _request_headers(
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        data: bytes | None,
        headers: dict[str, str] | None,
        retry_scope: str,
    ) -> dict[str, str]:
        request_headers = {"Accept": "application/json", **(headers or {})}
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        if method.upper() != "POST":
            return request_headers
        content = (
            data
            if data is not None
            else json.dumps(payload or {}, sort_keys=True).encode("utf-8")
        )
        identity_headers = {
            key.casefold(): str(value)
            for key, value in request_headers.items()
            if key.casefold() != "opc-retry-token"
        }
        retry_identity = json.dumps(
            {
                "method": method.upper(),
                "path": path,
                "scope": retry_scope,
                "headers": identity_headers,
                "content_sha256": hashlib.sha256(content).hexdigest(),
            },
            sort_keys=True,
        )
        request_headers["opc-retry-token"] = str(
            uuid.uuid5(uuid.NAMESPACE_URL, retry_identity)
        )
        return request_headers


    @staticmethod
    def _response_body(response: Any) -> Any:
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.content


    @staticmethod
    def _safe_diagnostic_fragment(value: Any, *, limit: int = 240) -> str:
        if not isinstance(value, str):
            return ""
        fragment = " ".join(value.split())
        fragment = re.sub(r"https?://\S+", "[url]", fragment, flags=re.IGNORECASE)
        fragment = re.sub(r"\bocid1\.[A-Za-z0-9._-]+", "[ocid]", fragment)
        fragment = re.sub(
            r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
            "[email]",
            fragment,
            flags=re.IGNORECASE,
        )
        fragment = re.sub(
            r"(?i)\b(authorization|credential|password|secret|token)\s*[:=]\s*\S+",
            r"\1=[redacted]",
            fragment,
        )
        return fragment[:limit]


    @classmethod
    def _request_error_message(
        cls,
        method: str,
        path: str,
        phase: str,
        request_headers: dict[str, str],
        response: Any,
    ) -> str:
        endpoint = re.sub(r"(/workspaces/)[^/]+", r"\1{workspace}", path)
        endpoint = cls._safe_diagnostic_fragment(endpoint, limit=180)
        target = cls._safe_diagnostic_fragment(request_headers.get("path"), limit=180)
        body = cls._response_body(response)
        code = cls._safe_diagnostic_fragment(
            body.get("code") if isinstance(body, dict) else None, limit=80
        )
        message = cls._safe_diagnostic_fragment(
            body.get("message") if isinstance(body, dict) else None
        )
        context = f"{method.upper()} {endpoint} during {phase}"
        if target:
            context += f" for {target}"
        oracle_detail = ""
        if code or message:
            oracle_detail = f" Oracle {code or 'error'}: {message or 'No message provided.'}"
        return (
            f"AIDP rejected {context} ({response.status_code}).{oracle_detail} "
            "Check the AIDP policy and request contract, then retry."
        )


    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        allow_not_found: bool = False,
        include_headers: bool = False,
        phase: str = "content",
        retry_scope: str = "",
        legacy: bool = False,
    ) -> Any:
        request_headers = self._request_headers(
            method, path, payload, data, headers, retry_scope
        )
        try:
            with self._session_lock:
                response = self.session.request(
                    method,
                    f"{self.base.replace('/20260430/aiDataPlatforms/', '/20240831/dataLakes/', 1) if legacy else self.base}{path}",
                    auth=self.signer,
                    json=payload,
                    data=data,
                    headers=request_headers,
                    params=params,
                    timeout=(10, 60),
                    allow_redirects=False,
                )
        except requests.exceptions.RequestException as exc:
            raise AidpProvisionPending(
                "AIDP is still accepting the requested material. Retry shortly.", phase
            ) from exc
        if response.status_code in {408, 409, 429, 500, 502, 503, 504}:
            raise AidpProvisionPending("AIDP is still reconciling the requested material. Retry shortly.", phase)
        if response.status_code == 404 and allow_not_found:
            return (None, response.headers) if include_headers else None
        if response.status_code >= 300:
            raise AidpRequestError(
                self._request_error_message(
                    method, path, phase, request_headers, response
                ), method=method, path=path, phase=phase,
                status_code=response.status_code,
                request_id=str(response.headers.get("opc-request-id") or ""),
            )
        result = self._response_body(response)
        return (result, response.headers) if include_headers else result


    @staticmethod
    def _page_items(body: Any) -> list[dict[str, Any]]:
        if isinstance(body, list):
            values = body
        elif isinstance(body, dict) and ("items" in body or "Items" in body):
            values = body.get("items") if "items" in body else body.get("Items")
        else:
            raise AidpProvisionError("AIDP returned an invalid paginated response.")
        if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
            raise AidpProvisionError("AIDP returned invalid list items.")
        return values


    def _list(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        phase: str = "content",
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page: str | None = None
        seen_pages: set[str] = set()
        for _ in range(1000):
            query = {"limit": "100", **(params or {})}
            if page:
                query["page"] = page
            body, response_headers = self._request(
                "GET", path, params=query, include_headers=True, phase=phase
            )
            items.extend(self._page_items(body))
            page = response_headers.get("opc-next-page") or response_headers.get("Opc-Next-Page")
            if not page:
                return items
            if page in seen_pages:
                raise AidpProvisionError("AIDP returned a repeated pagination token.")
            seen_pages.add(page)
        raise AidpProvisionError("AIDP pagination exceeded the safety limit.")


    def _workspace(self) -> dict[str, Any]:
        if getattr(self.settings, 'aidp_workspace_key', ''):
            key = self.settings.aidp_workspace_key
            workspace = self._request('GET', '/workspaces/' + quote(key, safe=''), phase='workspace')
            if not isinstance(workspace, dict) or workspace.get('key') != key:
                raise AidpProvisionConflict('The selected workspace identity differs from its exact binding.')
            return self._require_operational_state(workspace, {'ACTIVE'}, 'workspace', 'workspace')
        workspaces = [
            item
            for item in self._list("/workspaces", phase="workspace")
            if item.get("displayName") == self.settings.aidp_workspace_name
        ]
        if len(workspaces) != 1:
            raise AidpProvisionPending("The default AIDP workspace is not ready yet. Retry shortly.", "workspace")
        return self._require_operational_state(
            workspaces[0], {"ACTIVE"}, "workspace", "workspace"
        )


    def _agents(self, workspace_key: str, name: str) -> list[dict[str, Any]]:
        names = {name}
        return [
            item
            for item in self._list(
                f"/workspaces/{workspace_key}/agents", phase="content"
            )
            if self._resource_name(item) in names
        ]


    def _agent_deployments(self, path: str) -> list[dict[str, Any]]:
        deployments = [
            item for item in self._list(path, phase="deployment")
            if str(item.get("lifecycleState") or item.get("state") or "").upper()
            not in {"INACTIVE", "DELETED"}
        ]
        if len(deployments) > 1:
            if any(
                str(item.get("lifecycleState") or item.get("state") or "").upper()
                in {"CREATING", "DEPLOYING", "UPDATING"}
                for item in deployments
            ):
                raise AidpProvisionPending(
                    "The global governance Agent deployment transition is still running.",
                    "deployment",
                )
            raise AidpProvisionError("AIDP has duplicate global governance Agent deployments.")
        return deployments


    @staticmethod
    def _require_operational_state(
        resource: dict[str, Any],
        allowed: set[str],
        kind: str,
        phase: str,
    ) -> dict[str, Any]:
        state = str(resource.get("lifecycleState") or resource.get("state") or "").upper()
        if state in allowed:
            return resource
        if state in {"FAILED", "DELETING", "DELETED", "DELETE_FAILED", "CANCELED", "CANCELLED"} or state.endswith("FAILED"):
            raise AidpProvisionError(f"The AIDP {kind} is in terminal state {state or 'UNKNOWN'}.")
        raise AidpProvisionPending(f"The AIDP {kind} is not operational yet. Retry shortly.", phase)


    def _workspace_object(
        self,
        workspace_key: str,
        path: str,
        *,
        phase: str,
    ) -> tuple[Any, dict[str, Any]]:
        body, headers = self._request(
            "GET",
            f"/workspaces/{workspace_key}/objects/{quote(path, safe='')}",
            headers={"Accept": "*/*"},
            allow_not_found=True,
            include_headers=True,
            phase=phase,
        )
        return body, headers


    @staticmethod
    def _workspace_object_exists(body: Any, headers: dict[str, Any]) -> bool:
        return body is not None or any(
            str(value)
            for name, value in headers.items()
            if name.casefold() in {"object-key", "object-type", "type"}
        )


    @staticmethod
    def _workspace_object_type(body: Any, headers: dict[str, Any]) -> str:
        for name, value in headers.items():
            if name.casefold() in {"object-type", "type"}:
                return str(value).upper()
        if isinstance(body, dict) and body.get("type") in {"FILE", "FOLDER"}:
            return str(body["type"])
        return ""


    @staticmethod
    def _content_matches(body: Any, expected: bytes) -> bool:
        if isinstance(body, bytes):
            actual = body
        elif isinstance(body, str):
            actual = body.encode("utf-8")
        elif isinstance(body, (dict, list)):
            try:
                parsed_expected = json.loads(expected)
            except (ValueError, UnicodeDecodeError):
                return False
            actual = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
            expected = json.dumps(parsed_expected, sort_keys=True, separators=(",", ":")).encode("utf-8")
        else:
            return False
        return actual == expected


    def _ensure_folder(self, workspace_key: str, path: str) -> bool:
        body, headers = self._workspace_object(workspace_key, path, phase="workspace")
        if self._workspace_object_exists(body, headers):
            if self._workspace_object_type(body, headers) not in {"", "FOLDER"}:
                raise AidpProvisionError(f"Workspace path {path} exists but is not a folder.")
            return False
        self._request(
            "POST",
            f"/workspaces/{workspace_key}/objects",
            data=b"",
            headers={
                "Accept": "*/*",
                "path": path,
                "type": "FOLDER",
                "is-overwrite": "false",
                "Content-Type": "application/octet-stream",
            },
            phase="workspace",
        )
        body, headers = self._workspace_object(workspace_key, path, phase="workspace")
        if not self._workspace_object_exists(body, headers):
            raise AidpProvisionPending(f"AIDP has not published workspace folder {path} yet.", "workspace")
        return True


    def _upload_file(
        self,
        workspace_key: str,
        path: str,
        content: bytes,
        *,
        repair_drift: bool = True,
    ) -> bool:
        body, headers = self._workspace_object(workspace_key, path, phase="content")
        exists = self._workspace_object_exists(body, headers)
        if exists and self._workspace_object_type(body, headers) == "FOLDER":
            raise AidpProvisionError(f"Workspace path {path} exists but is not a file.")
        if exists and (self._content_matches(body, content) or not repair_drift):
            return False
        self._request(
            "POST",
            f"/workspaces/{workspace_key}/objects",
            data=content,
            headers={
                "Accept": "*/*",
                "path": path,
                "type": "FILE",
                "is-overwrite": str(exists).lower(),
                "Content-Type": "application/octet-stream",
            },
            phase="content",
        )
        body, _ = self._workspace_object(workspace_key, path, phase="content")
        if not self._content_matches(body, content):
            raise AidpProvisionPending(f"AIDP has not published workspace file {path} yet.", "content")
        return True


    def _workspace_json(
        self,
        workspace_key: str,
        path: str,
        invalid_message: str,
    ) -> dict[str, Any] | None:
        body, headers = self._workspace_object(workspace_key, path, phase="workspace")
        if not self._workspace_object_exists(body, headers):
            return None
        if self._workspace_object_type(body, headers) == "FOLDER":
            raise AidpProvisionError(invalid_message)
        try:
            if isinstance(body, dict):
                payload = body
            elif isinstance(body, bytes):
                payload = json.loads(body.decode("utf-8"))
            elif isinstance(body, str):
                payload = json.loads(body)
            else:
                raise TypeError("unsupported control manifest body")
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AidpProvisionError(invalid_message) from exc
        if not isinstance(payload, dict):
            raise AidpProvisionError(invalid_message)
        return payload


    @staticmethod
    def _resource_name(resource: dict[str, Any]) -> str:
        return str(resource.get("displayName") or resource.get("name") or "")


    @staticmethod
    def _permission_grantee(item: dict[str, Any]) -> tuple[str, str]:
        grantee = item.get("grantee")
        if isinstance(grantee, dict):
            target = grantee.get("target")
            grantee_type = grantee.get("type")
        else:
            target = grantee
            grantee_type = None
        return (
            str(target or item.get("granteeName") or ""),
            str(grantee_type or item.get("granteeType") or "").upper(),
        )


    @staticmethod
    def _permission_values(item: dict[str, Any]) -> set[str]:
        return set(item.get("granteePermissions") or item.get("permissions") or [])


    def _ensure_workspace_layout(
        self,
        workspace_key: str,
        paths: tuple[str, ...],
    ) -> bool:
        changed = False
        for path in paths:
            changed = self._ensure_folder(workspace_key, path) or changed
        return changed


    def _role(self, name: str) -> dict[str, Any]:
        matches = [item for item in self._list("/roles", params={"displayName": name}, phase="permissions") if self._resource_name(item) == name]
        if len(matches) != 1 or not matches[0].get("key"):
            raise AidpProvisionPending(f"The AIDP role {name} is not ready yet.", "permissions")
        return matches[0]


    def _role_principal_ocids(self, name: str) -> tuple[set[str], set[str]]:
        role = self._role(name)
        pending = [str(role["key"])]
        visited: set[str] = set()
        users: set[str] = set()
        groups: set[str] = set()
        while pending:
            role_key = pending.pop()
            if role_key in visited:
                continue
            if len(visited) >= 100:
                raise AidpProvisionError("The AIDP administrator role hierarchy is too deep.")
            visited.add(role_key)
            details = self._request(
                "GET",
                f"/roles/{quote(role_key, safe='')}",
                phase="permissions",
            )
            assignees = details.get("assignees") if isinstance(details, dict) else None
            if not isinstance(assignees, list):
                raise AidpProvisionError("AIDP returned invalid administrator role assignees.")
            for item in assignees:
                if not isinstance(item, dict):
                    raise AidpProvisionError("AIDP returned an invalid administrator role assignee.")
                principal_type = str(item.get("type") or "").upper()
                target = str(item.get("target") or "")
                if principal_type == "USER" and target.startswith("ocid1.user."):
                    users.add(target)
                elif principal_type == "GROUP" and target.startswith("ocid1.group."):
                    groups.add(target)
                elif principal_type == "ROLE" and target:
                    pending.append(target)
                else:
                    raise AidpProvisionError("AIDP returned an unsupported administrator role assignee.")
        return users, groups


    def _role_user_ocids(self, name: str) -> set[str]:
        return self._role_principal_ocids(name)[0]


    async def platform_admin_user_ocids(self) -> set[str]:
        return await asyncio.to_thread(self._role_user_ocids, "AI_DATA_PLATFORM_ADMIN")


    async def platform_admin_principals(self) -> tuple[set[str], set[str]]:
        return await asyncio.to_thread(
            self._role_principal_ocids, "AI_DATA_PLATFORM_ADMIN"
        )


    async def is_platform_admin(self, user_ocid: str) -> bool:
        return user_ocid in await self.platform_admin_user_ocids()


    def _ensure_governance_bucket(self) -> bool:
        try:
            self.object_storage.head_bucket(
                self.settings.objectstorage_namespace, self.settings.artifacts_bucket_name
            )
            return False
        except self._oci.exceptions.ServiceError as exc:
            if exc.status != 404:
                raise AidpProvisionError("The configured governance artifacts bucket is unavailable.") from exc
        raise AidpProvisionConflict('The exact artifacts bucket is missing. Reconcile Terraform ownership; the installer never creates a replacement bucket.')


    def _credential_payload(self) -> dict[str, Any]:
        key_path = Path(str(self._oci_config.get("key_file") or ""))
        if not key_path.is_absolute():
            key_path = Path(self.settings.oci_config_file).parent / key_path
        try:
            private_key = key_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise AidpProvisionError("The dedicated governance OCI credential cannot be loaded.") from exc
        required = {name: str(self._oci_config.get(name) or "") for name in ("tenancy", "user", "fingerprint")}
        if any(not value for value in required.values()) or not private_key.strip():
            raise AidpProvisionError("The dedicated governance OCI credential is incomplete.")
        return {
            "displayName": GOVERNANCE_CREDENTIAL_NAME,
            "credentialDescription": "Shared OCI credential for governance and Gods Eye View runtimes",
            "type": "SECRET_TOKEN",
            "credentialDetails": {
                "credentialType": "SECRET_TOKEN",
                "secretTokenPair": [
                    {"secretKey": name, "secretValue": value}
                    for name, value in (
                        ("tenancy", required["tenancy"]),
                        ("user", required["user"]),
                        ("fingerprint", required["fingerprint"]),
                        ("region", self.settings.aidp_region),
                        ("private_key", private_key),
                    )
                ],
            },
        }


    def _ensure_governance_credential(self) -> tuple[str, bool]:
        credential = self._shared_oci_credential()
        created = credential is None
        if credential is None:
            self._request("POST", "/credentials", payload=self._credential_payload(), phase="control")
            credential = self._shared_oci_credential()
            if credential is None:
                raise AidpProvisionPending("AIDP has not published the governance credential yet.", "control")
        return str(credential.get("key") or credential["id"]), created


    def _shared_oci_credential(self) -> dict[str, Any] | None:
        try:
            return shared_credential(self._list("/credentials", phase="control"))
        except RuntimeError as exc:
            raise AidpProvisionError(str(exc)) from exc


    @classmethod
    def _role_permission_matches(
        cls,
        item: dict[str, Any],
        role_name: str,
        permission: str,
        inheritable: bool | None = None,
        *,
        include_columns: bool = False,
    ) -> bool:
        grantee, grantee_type = cls._permission_grantee(item)
        values = cls._permission_values(item)
        return bool(
            grantee == role_name
            and grantee_type == "ROLE"
            and values == {permission}
            and item.get("isInherited") is not True
            and (inheritable is None or item.get("isPermissionsInheritable") is inheritable)
            and (
                not include_columns
                or not (item.get("columns") or item.get("includeColumns"))
                and not item.get("excludeColumns")
            )
        )


    def _ensure_role_permission(
        self,
        resource_path: str,
        assignment_key: str,
        role_name: str,
        permission: str,
        *,
        inheritable: bool | None = None,
        include_columns: bool = False,
    ) -> bool:
        path = f"{resource_path}/permissions"
        current = self._list(path, phase="permissions")
        matches = [
            item
            for item in current
            if item.get("isInherited") is not True
            and self._permission_grantee(item) == (role_name, "ROLE")
        ]
        if any(
            not self._role_permission_matches(
                item,
                role_name,
                permission,
                inheritable,
                include_columns=include_columns,
            )
            for item in matches
        ) or len(matches) > 1:
            raise AidpProvisionError(f"Role {role_name} has a conflicting direct governance permission.")
        if any(
            self._role_permission_matches(
                item,
                role_name,
                permission,
                inheritable,
                include_columns=include_columns,
            )
            for item in matches
        ):
            return False
        assignment: dict[str, Any] = {
            "assignees": {"type": "ROLE", "targets": [role_name]},
            "permissions": [permission],
        }
        if inheritable is not None:
            assignment["isPermissionsInheritable"] = inheritable
        if include_columns:
            assignment.update(includeColumns=[], excludeColumns=[])
        self._request(
            "POST",
            f"{resource_path}/actions/managePermission",
            payload={assignment_key: assignment},
            phase="permissions",
        )
        current = self._list(path, phase="permissions")
        if not any(
            self._role_permission_matches(
                item,
                role_name,
                permission,
                inheritable,
                include_columns=include_columns,
            )
            for item in current
        ):
            raise AidpProvisionPending("AIDP has not applied the governance permission yet.", "permissions")
        return True
