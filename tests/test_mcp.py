import asyncio
from assetrag import mcp_server

REQUIRED = {"search_assets", "get_asset", "check_license", "import_asset", "harvest_code", "save_lesson", "search_lessons",
            "audit_project", "library_stats", "find_knowledge", "get_knowledge", "get_rules", "scan_code", "record_failure",
            "promote_failure_to_rule", "knowledge_stats", "route_task"}


def test_all_tools_registered_with_real_input_schemas():
    tools = {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert REQUIRED <= set(tools)
    # regression: a *a/**k wrapper made every tool demand arguments named "a" and "k"
    for t in tools.values():
        props = set(t.inputSchema.get("properties", {}))
        assert not ({"a", "k"} & props), t.name
    assert tools["library_stats"].inputSchema.get("required", []) == []
    assert "error" in tools["find_knowledge"].inputSchema["properties"]
    assert tools["get_asset"].inputSchema["required"] == ["asset_id"]


def test_instructions_name_every_playbook_and_snippet_matches():
    from pathlib import Path
    from assetrag import mcp_server
    from assetrag.seeds.playbooks import PLAYBOOKS
    missing = [p["key"] for p in PLAYBOOKS if p["key"] not in mcp_server.INSTRUCTIONS]
    assert not missing, f"playbooks the agent is never told about: {missing}"
    snippet = (Path(__file__).resolve().parents[1] / "integrations" / "CLAUDE.snippet.md").read_text()
    assert mcp_server.INSTRUCTIONS in snippet, "integrations/CLAUDE.snippet.md is out of sync with mcp_server.INSTRUCTIONS"
    for tool in ("search_assets", "find_knowledge", "get_rules", "scan_code", "check_license", "record_failure", "save_lesson", "harvest_code", "import_asset"):
        assert tool in mcp_server.INSTRUCTIONS
