"""Validated deployment bindings; credentials remain external files."""
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    oci_config_file: str
    aidp_region: str
    aidp_platform_id: str
    aidp_workspace_name: str = ''
    aidp_workspace_key: str = ''
    compartment_id: str
    objectstorage_namespace: str
    artifacts_bucket_name: str
    agent_model_id: str
    llm_auth_mode: Literal['oci_iam', 'openai'] = 'oci_iam'
    identity_domain_id: str
    admin_user_ocid: str

    @field_validator('admin_user_ocid', 'identity_domain_id', 'aidp_platform_id', 'compartment_id')
    @classmethod
    def ocid(cls, value):
        if not re.fullmatch(r'ocid1\.[a-z_]+\.[A-Za-z0-9._-]+', value):
            raise ValueError('A valid resource OCID is required')
        return value

    @field_validator('aidp_region')
    @classmethod
    def region(cls, value):
        if not re.fullmatch(r'[a-z]{2}-[a-z]+-\d+', value):
            raise ValueError('Invalid OCI region')
        return value

    @field_validator('aidp_workspace_key')
    @classmethod
    def workspace_key(cls, value):
        if value and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
            raise ValueError('Invalid workspace key')
        return value
