import fs from "node:fs";
import process from "node:process";

const SQL_RESPONSE_SCHEMA = {
  type: "object",
  properties: {
    sql: { type: "string" },
    used_tables: { type: "array", items: { type: "string" } },
    assumptions: { type: "array", items: { type: "string" } },
    confidence: { type: "number", minimum: 0, maximum: 1 },
  },
  required: ["sql", "used_tables", "assumptions", "confidence"],
  additionalProperties: false,
};

function requestedModel(value) {
  const model = String(value || "").trim();
  return model && !["none", "default", "auto"].includes(model.toLowerCase()) ? model : "";
}

function splitModel(value, aliases = {}) {
  const model = requestedModel(value);
  if (!model) return undefined;
  const slash = model.indexOf("/");
  if (slash < 1) return { providerID: aliases.default || "", modelID: model };
  const prefix = model.slice(0, slash);
  return {
    providerID: aliases[prefix] || prefix,
    modelID: model.slice(slash + 1),
  };
}

function withAbort(timeoutSec) {
  const controller = new AbortController();
  const timeoutMs = Math.max(1, Number(timeoutSec || 30) * 1000);
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return {
    signal: controller.signal,
    clear() {
      clearTimeout(timer);
    },
  };
}

function textParts(parts) {
  return (Array.isArray(parts) ? parts : [])
    .filter((part) => part && part.type === "text" && typeof part.text === "string")
    .map((part) => part.text)
    .join("")
    .trim();
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error || "unknown error");
}

function providerError(provider, error) {
  const message = errorMessage(error);
  if (/^(LLM_|PI_|CODEX_|OPENCODE_)/.test(message)) return message;
  if (message === "LLM_TIMEOUT" || error?.name === "AbortError") return "LLM_TIMEOUT";
  if (provider === "pi" && /no available|not configured|auth/i.test(message)) {
    return "PI_AUTH_NOT_CONFIGURED";
  }
  if (provider === "codex" && /ENOENT|not found|spawn codex/i.test(message)) {
    return "CODEX_CLI_NOT_INSTALLED";
  }
  if (provider === "opencode" && /ENOENT|not found|spawn opencode/i.test(message)) {
    return "OPENCODE_CLI_NOT_INSTALLED";
  }
  return message.startsWith("LLM_") ? message : "LLM_PROVIDER_ERROR";
}

async function callPi(request) {
  const { ModelRuntime } = await import("@earendil-works/pi-coding-agent");
  const runtime = await ModelRuntime.create({ allowModelNetwork: false });
  const requested = requestedModel(request.model) || requestedModel(process.env.PI_MODEL);
  let model;

  if (requested) {
    const selected = splitModel(requested, {
      codex: "openai-codex",
      opencode: "opencode",
      default: "",
    });
    if (selected?.providerID) {
      model = runtime.getModel(selected.providerID, selected.modelID);
    } else {
      model = runtime.getModels().find((candidate) => candidate.id === selected.modelID);
    }
  } else {
    const available = await runtime.getAvailable();
    model = available[0];
  }

  if (!model) throw new Error("PI_MODEL_NOT_AVAILABLE");

  const timeout = withAbort(request.timeoutSec);
  try {
    const response = await runtime.completeSimple(
      model,
      {
        messages: [{ role: "user", content: request.prompt, timestamp: Date.now() }],
      },
      { reasoning: "off", timeoutMs: Number(request.timeoutSec || 30) * 1000, signal: timeout.signal },
    );
    if (response.stopReason === "error") throw new Error(response.errorMessage || "LLM_PROVIDER_ERROR");
    const text = textParts(response.content);
    if (!text) throw new Error("LLM_BAD_RESPONSE");
    return text;
  } finally {
    timeout.clear();
  }
}

async function callCodex(request) {
  const { Codex } = await import("@openai/codex-sdk");
  const options = {};
  if (process.env.CODEX_BASE_URL) options.baseUrl = process.env.CODEX_BASE_URL;
  if (process.env.CODEX_API_KEY) options.apiKey = process.env.CODEX_API_KEY;
  const codex = new Codex(options);
  const timeout = withAbort(request.timeoutSec);
  try {
    const model = requestedModel(request.model);
    const thread = codex.startThread({
      model: model?.replace(/^(codex|openai-codex)\//, "") || undefined,
      sandboxMode: "read-only",
      approvalPolicy: "never",
      networkAccessEnabled: false,
      webSearchMode: "disabled",
      workingDirectory: request.cwd || process.cwd(),
    });
    const turn = await thread.run(request.prompt, {
      outputSchema: SQL_RESPONSE_SCHEMA,
      signal: timeout.signal,
    });
    if (!turn.finalResponse) throw new Error("LLM_BAD_RESPONSE");
    return turn.finalResponse;
  } finally {
    timeout.clear();
  }
}

async function callOpencode(request) {
  const { createOpencode } = await import("@opencode-ai/sdk");
  const selected = splitModel(request.model, { default: "opencode" });
  const config = {
    permission: {
      edit: "deny",
      bash: "deny",
      webfetch: "deny",
      external_directory: "deny",
    },
  };
  if (selected) config.model = `${selected.providerID}/${selected.modelID}`;

  const timeout = withAbort(request.timeoutSec);
  let runtime;
  try {
    runtime = await createOpencode({
      timeout: Math.max(1000, Number(request.timeoutSec || 30) * 1000),
      config,
      signal: timeout.signal,
    });
    const created = await runtime.client.session.create({
      query: { directory: request.cwd || process.cwd() },
      body: { title: "WFERP SQL generation" },
      signal: timeout.signal,
    });
    if (!created.data) throw new Error("LLM_PROVIDER_ERROR");

    const promptBody = {
      parts: [{ type: "text", text: request.prompt }],
    };
    if (selected) {
      promptBody.model = {
        providerID: selected.providerID,
        modelID: selected.modelID,
      };
    }
    const response = await runtime.client.session.prompt({
      path: { id: created.data.id },
      query: { directory: request.cwd || process.cwd() },
      body: promptBody,
      signal: timeout.signal,
    });
    if (response.error || !response.data) throw new Error("LLM_PROVIDER_ERROR");
    const text = textParts(response.data.parts);
    if (!text) throw new Error("LLM_BAD_RESPONSE");
    return text;
  } finally {
    runtime?.server.close();
    timeout.clear();
  }
}

async function main() {
  const request = JSON.parse(fs.readFileSync(0, "utf8"));
  const provider = String(request.provider || "").trim().toLowerCase();
  if (!["pi", "codex", "opencode"].includes(provider)) {
    throw new Error("LLM_PROVIDER_UNSUPPORTED");
  }
  process.env.WFERP_LLM_PROVIDER = provider;
  if (request.cwd) process.chdir(request.cwd);

  const text = provider === "pi"
    ? await callPi(request)
    : provider === "codex"
      ? await callCodex(request)
      : await callOpencode(request);
  process.stdout.write(JSON.stringify({ text }) + "\n");
}

main().catch((error) => {
  const provider = String(process.env.WFERP_LLM_PROVIDER || "").trim().toLowerCase();
  const code = providerError(provider, error);
  process.stderr.write(JSON.stringify({ code, detail: errorMessage(error) }) + "\n");
  process.exitCode = 1;
});
