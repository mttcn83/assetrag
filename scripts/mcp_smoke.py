"""Drive the real MCP server over stdio like Claude Code does."""
import asyncio, json, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    p = StdioServerParameters(command=".venv/bin/assetrag-mcp")
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            print("tools:", tools)
            need = {"repo_map","find_knowledge","get_knowledge","get_rules","scan_code","record_failure","promote_failure_to_rule","knowledge_stats","route_task","search_assets","get_asset","check_license","import_asset","harvest_code","save_lesson","search_lessons","audit_project","library_stats"}
            assert need <= set(tools), need - set(tools)
            async def call(n, **a):
                res = await s.call_tool(n, a); return json.loads(res.content[0].text)
            st = await call("library_stats"); print("total", st["total"])
            hits = await call("search_assets", query="trash can delete icon", category="ui", limit=3)
            print([ (h["id"], h["name"]) for h in hits["results"]])
            if hits["results"]:
                a = await call("get_asset", asset_id=hits["results"][0]["id"])
                print("license", a["license"], "| files", list(a["files"])[:2])
                print("check", (await call("check_license", asset_id=a["id"]))["ok"])
            k = await call("find_knowledge", error="TypeError: 'NoneType' object is not iterable", limit=1)
            print("bugfix ->", k["results"][0]["title"], "|", k["results"][0]["match"], "|", k["results"][0]["verification"])
            r = await call("find_knowledge", query="retry flaky api calls with backoff", kinds=["recipe"], language="python", limit=1)
            print("recipe ->", r["results"][0]["title"])
            print("rules ->", len((await call("get_rules", task="write an http client", language="python"))["must"]), "must-rules")
            print("scan ->", (await call("scan_code", text="k = 'AKIAABCDEFGHIJKLMNOP'"))["count"], "finding")
            print("route ->", [c["tool"] for c in (await call("route_task", task="TypeError crash in my React dashboard page"))["suggested"]])
            print("kstats ->", (await call("knowledge_stats"))["total_active"], "entries")
            print("bad id ->", await call("get_asset", asset_id="../../etc/passwd"))
asyncio.run(main())
