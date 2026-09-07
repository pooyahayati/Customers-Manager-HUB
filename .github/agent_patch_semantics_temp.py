from pathlib import Path


agents = Path("apps/api/src/customers_manager_hub/agents.py")
content = agents.read_text()
old_validator = '''    @model_validator(mode="after")
    def require_change(self) -> Self:
        if (
            self.name is None
            and self.prompt_id is None
            and self.description is None
            and self.is_active is None
        ):
            raise ValueError("At least one Agent setting must be supplied")
        return self
'''
new_validator = '''    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one Agent setting must be supplied")
        for field_name in ("name", "prompt_id", "is_active"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} must not be null")
        return self
'''
if old_validator not in content:
    raise SystemExit("AgentUpdate validator block not found")
content = content.replace(old_validator, new_validator, 1)
old_description = '''    if payload.description is not None and payload.description != agent.description:
        agent.description = payload.description
        changed_fields.append("description")
'''
new_description = '''    if "description" in payload.model_fields_set and payload.description != agent.description:
        agent.description = payload.description
        changed_fields.append("description")
'''
if old_description not in content:
    raise SystemExit("Agent description update block not found")
content = content.replace(old_description, new_description, 1)
agents.write_text(content)


test = Path("apps/api/tests/test_agent_prompt_integration.py")
content = test.read_text()
old_admin = '''        admin_update = client.patch(
            f"/api/v1/tenants/{tenant_a}/agents/{agent_id}",
            json={"description": "Admin can update"},
        )
        assert admin_update.status_code == 200

        for role, email in (
'''
new_admin = '''        admin_update = client.patch(
            f"/api/v1/tenants/{tenant_a}/agents/{agent_id}",
            json={"description": "Admin can update"},
        )
        assert admin_update.status_code == 200
        assert admin_update.json()["description"] == "Admin can update"

        cleared_description = client.patch(
            f"/api/v1/tenants/{tenant_a}/agents/{agent_id}",
            json={"description": None},
        )
        assert cleared_description.status_code == 200
        assert cleared_description.json()["description"] is None

        empty_patch = client.patch(
            f"/api/v1/tenants/{tenant_a}/agents/{agent_id}",
            json={},
        )
        assert empty_patch.status_code == 422
        null_name = client.patch(
            f"/api/v1/tenants/{tenant_a}/agents/{agent_id}",
            json={"name": None},
        )
        assert null_name.status_code == 422

        for role, email in (
'''
if old_admin not in content:
    raise SystemExit("admin update test marker not found")
content = content.replace(old_admin, new_admin, 1)
test.write_text(content)
