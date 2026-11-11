# AI Market Intelligence

Python CLI/hooks and AIDP artifacts only. No portal, HTTP server, frontend or administrative VM.
Source provenance: Starter Kits commit 3c82617fd298933995e954955841007253cc0985.
Use docs/architecture.md, docs/operations.md and docs/quality.md as the functional product contract.
Use protected credential providers for OCI and Apify; never expose or commit keys, tokens, .oci contents or private state.
The SDK/CLI may read this project's .oci/config and its referenced key to sign authorized development requests. Use them in process only, without printing, copying or changing credentials.
During authorized development, processes may read installation state and read/back up operation receipts for diagnosis, migration and recovery. Keep backups in ignored private storage; report only necessary identifiers, paths, hashes and sanitized outcomes.
The operator signs OCI requests; admin_user_ocid is an authorized resource owner, not an impersonation credential.
Development TEST research, configured model calls and paid actors are authorized without a consumption limit. Preserve exact resource identities, installer v2 recovery and durable effect records; reconcile uncertain runs before retrying. This development authorization does not authorize production publication or unrelated workloads.
Prefer the AIDP Agent Flow LangGraph TEST skill and the existing local harness for focused iteration; verify the same served candidate in native AIDP before acceptance. Keep automated regression fixtures free of external effects.
Run scripts/arch-preflight.ps1 before code changes and scripts/arch-postflight.ps1 afterward.
One Sentrux CLI baseline per task. Preserve the existing baseline and enforce .sentrux/rules.toml through scripts/arch-scan.ps1; report regression gate and rule-check results separately. Keep the complete Python import boundary checks in scripts/check_repository.py alongside the resolved dependency rules.
Candidate extraction alone does not authorize cloud mutations or release publication. Explicitly authorized AIDP development may upload, configure and refresh the existing TEST candidate; production and GitHub publication follow their separately established scope. Own only this repository unless the user explicitly includes plugin maintenance.
