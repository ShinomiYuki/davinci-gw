"""使用明确指定的 DSH 安装包验证真实 MCP 插件，不改客户端配置。"""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.slow
def test_dsh_plugin_discovers_gateway_and_calls_capabilities(tmp_path):
    application = os.environ.get("DAVINCI_GW_DSH_APP")
    executable = os.environ.get("DAVINCI_GW_MCP_EXE")
    node = shutil.which("node")
    if not application or not executable or not node:
        pytest.skip("设置 DAVINCI_GW_DSH_APP、DAVINCI_GW_MCP_EXE 并提供 Node 后验证实际 DSH 插件")
    project = Path(__file__).resolve().parents[2]
    home = tmp_path / "dsh-home"
    arguments = json.loads(os.environ.get("DAVINCI_GW_MCP_ARGS", "[]"))
    quoted = lambda value: "'" + str(value).replace("'", "''") + "'"
    argument_expression = "@(" + ",".join(quoted(value) for value in arguments) + ")"
    command = (f"& {quoted(project / 'scripts' / 'configure_mcp_client.ps1')} -Client DeepSeekHarness "
               f"-Command {quoted(executable)} -DshHome {quoted(home)} -Arguments {argument_expression}")
    subprocess.run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", command],
                   check=True, capture_output=True, timeout=30)
    scenario = tmp_path / "dsh-acceptance.mjs"
    scenario.write_text("""
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import assert from 'node:assert/strict';
const root = process.env.DAVINCI_GW_DSH_APP;
const load = path => import(pathToFileURL(resolve(root, path)).href);
const yaml = (await load('node_modules/js-yaml/index.js')).default;
const patch = yaml.load(readFileSync(process.env.DSH_TEST_PATCH, 'utf8'));
const entry = patch[0].insert[0];
assert.equal(entry.name, 'mcp-client');
assert.equal(entry.config.command, process.env.DAVINCI_GW_MCP_EXE);
const plugin = await load('node_modules/@deepseek-ai/dsh-mcp-client/lib/index.js');
const config = plugin.Config(entry.config);
const tools = new Map();
const effects = [];
const ctx = {
  root: {},
  logger: { info() {}, warn() {}, error(message) { console.error(message); } },
  effect(callback) { const dispose = callback(); effects.push(dispose); return dispose; },
  tools: { register(definition) {
    assert(!tools.has(definition.name));
    tools.set(definition.name, definition);
    return () => tools.delete(definition.name);
  } }
};
try {
  await plugin.apply(ctx, { ...config, failOnStartupError: true });
  assert.equal(tools.size, 9);
  const tool = tools.get('mcp__davinci_gateway__get_gateway_capabilities');
  assert(tool);
  const result = await tool.execute({}, { signal: new AbortController().signal });
  assert.equal(result.structuredContent.status, 'SUCCESS');
  assert.equal(result.structuredContent.product.version, process.env.DSH_TEST_VERSION);
  console.log(JSON.stringify({ status: 'SUCCESS', tools: tools.size, version: result.structuredContent.product.version }));
} finally {
  for (const dispose of effects.reverse()) await dispose?.();
}
""", encoding="utf-8")
    import tomllib
    version = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    environment = {**os.environ, "DSH_TEST_PATCH": str(home / "cordis.patch.yml"), "DSH_TEST_VERSION": version}
    result = subprocess.run([node, str(scenario)], env=environment, check=True,
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert json.loads(result.stdout.strip())["status"] == "SUCCESS"
