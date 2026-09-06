from pathlib import Path


worker = Path("apps/api/src/customers_manager_hub/worker.py")
content = worker.read_text()
if "async def _process_job(" not in content:
    raise SystemExit("worker private job boundary not found")
content = content.replace("async def _process_job(", "async def process_job(", 1)
content = content.replace("await _process_job(", "await process_job(")
worker.write_text(content)


test_path = Path("apps/api/tests/test_agent_prompt_integration.py")
content = test_path.read_text()
if "from customers_manager_hub.worker import _process_job" not in content:
    raise SystemExit("test private worker import not found")
content = content.replace(
    "from customers_manager_hub.worker import _process_job",
    "from customers_manager_hub.worker import process_job",
    1,
)
content = content.replace("await _process_job(", "await process_job(")
test_path.write_text(content)
