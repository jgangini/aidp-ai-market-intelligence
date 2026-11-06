"""Exact shared OCI credential selection, independent of viewer code."""
OCI_CREDENTIALS = ("AidpRuntime",)

def shared_credential(credentials):
    """Choose once at deployment; an invalid preferred credential never triggers fallback."""
    for name in OCI_CREDENTIALS:
        matches = [item for item in credentials if item.get("displayName") == name
                   and (item.get("lifecycleState") or item.get("lifeCycleState") or item.get("state")) != "DELETED"]
        if not matches:
            continue
        if len(matches) != 1:
            raise RuntimeError("Duplicate shared OCI credentials")
        item = matches[0]
        if (item.get("type") or item.get("credentialType")) != "SECRET_TOKEN":
            raise RuntimeError("Shared OCI credential has an incompatible type")
        if (item.get("lifecycleState") or item.get("lifeCycleState") or item.get("state")) != "ACTIVE" or not (item.get("key") or item.get("id")):
            raise RuntimeError("Shared OCI credential is not ready")
        return item
    return None
