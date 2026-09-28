const http = require("http");

const PORT = process.env.FAKE_VLM_PORT ? Number(process.env.FAKE_VLM_PORT) : 18001;

function parsePngDimensions(base64) {
  try {
    const buffer = Buffer.from(base64, "base64");
    const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    if (!buffer.subarray(0, 8).equals(PNG_SIGNATURE)) {
      return null;
    }
    // IHDR starts at offset 8: length (4 bytes), type "IHDR" (4 bytes), then width/height.
    const width = buffer.readUInt32BE(16);
    const height = buffer.readUInt32BE(20);
    return { width, height };
  } catch {
    return null;
  }
}

function findImageDimensions(messages) {
  for (const message of messages || []) {
    const content = message.content;
    if (Array.isArray(content)) {
      for (const part of content) {
        if (part.type === "image_url" && part.image_url?.url) {
          const url = part.image_url.url;
          const base64 = url.includes(",") ? url.split(",")[1] : url;
          const dims = parsePngDimensions(base64);
          if (dims) return dims;
        }
      }
    }
  }
  return null;
}

function defaultBoxes(messages) {
  const dims = findImageDimensions(messages);
  if (!dims) {
    return [{ x: 10, y: 10, width: 80, height: 80 }];
  }
  const { width, height } = dims;
  return [
    {
      x: Math.floor(width * 0.1),
      y: Math.floor(height * 0.1),
      width: Math.max(1, Math.floor(width * 0.8)),
      height: Math.max(1, Math.floor(height * 0.8)),
    },
  ];
}

function parseJson(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
    });
    req.on("end", () => {
      try {
        resolve(body ? JSON.parse(body) : {});
      } catch (err) {
        reject(err);
      }
    });
    req.on("error", reject);
  });
}

function sendJson(res, status, payload) {
  const data = JSON.stringify(payload);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(data),
  });
  res.end(data);
}

function classifyRequestType(systemPrompt, userPrompt) {
  const combined = `${systemPrompt || ""}\n${userPrompt || ""}`;
  if (combined.includes("detecting problem boxes") || combined.includes("Detect every distinct problem")) {
    return "detection";
  }
  if (combined.includes("classifying a study problem image as either math or english")) {
    return "classification";
  }
  if (combined.includes("grading a short-answer response")) {
    return "grading";
  }
  if (systemPrompt?.includes("generate one new math practice problem")) {
    return "variant-generator";
  }
  if (systemPrompt?.includes("independent math problem validator")) {
    return "variant-validator";
  }
  if (systemPrompt?.includes("compare answers to math problems for equivalence")) {
    return "variant-helper";
  }
  if (systemPrompt?.includes("solution writer") || userPrompt?.includes("Solve the problem")) {
    return "solution";
  }
  if (systemPrompt?.includes("tutor")) {
    return "coaching";
  }
  if (combined.includes("extracting") || combined.includes("Extract the study problem")) {
    return "extraction";
  }
  return "extraction";
}

function createCompletion(content) {
  return {
    id: "fake-completion",
    object: "chat.completion",
    created: Math.floor(Date.now() / 1000),
    model: "fake-model",
    choices: [
      {
        index: 0,
        message: { role: "assistant", content },
        finish_reason: "stop",
      },
    ],
  };
}

const state = {
  boxes: null,
  overrides: {},
  // Variant role scenarios (issue #629). Validators are addressed by the
  // model name from the profile config ("validator-1" / "validator-2").
  variants: {
    generator: "pass", // pass | fail | invalid
    "validator-1": "pass", // pass | unsolvable | invalid | fail | slow
    "validator-2": "pass",
    helper: "equivalent", // equivalent | different | uncertain | fail
  },
  variantCounts: {
    generator: 0,
    "validator-1": 0,
    "validator-2": 0,
    helper: 0,
  },
  grading: { count: 0, hadImage: false },
};

function fakeState() {
  return {
    variants: state.variants,
    variantCounts: state.variantCounts,
    grading: state.grading,
  };
}

function resetVariantState() {
  state.variants = {
    generator: "pass",
    "validator-1": "pass",
    "validator-2": "pass",
    helper: "equivalent",
  };
  state.variantCounts = { generator: 0, "validator-1": 0, "validator-2": 0, helper: 0 };
  state.grading = { count: 0, hadImage: false };
}

// The variant prompts embed the task as "Task data:\n{json}".
function parseTaskData(userPrompt) {
  const marker = "Task data:";
  const index = (userPrompt || "").indexOf(marker);
  if (index === -1) return null;
  try {
    return JSON.parse(userPrompt.slice(index + marker.length));
  } catch {
    return null;
  }
}

function hasGraph(value) {
  return Boolean((value || "").trim());
}

function validatorReport(task) {
  const sourceGraph = hasGraph(task?.source?.graphDsl);
  const candidateGraph = hasGraph(task?.candidate?.graphDsl);
  const graphCategory = sourceGraph || candidateGraph ? "consistent" : "not-applicable";
  const passing = {
    originalWellPosed: ["yes", "Fake evidence: original is well-posed."],
    variantWellPosed: ["yes", "Fake evidence: variant is well-posed."],
    coreKnowledge: ["preserved", "Fake evidence: same skill required."],
    solutionStructure: ["preserved", "Fake evidence: same solution steps."],
    quantityRoles: ["preserved", "Fake evidence: same quantity roles."],
    difficultyShift: ["comparable", "Fake evidence: comparable difficulty."],
    numericComplexityShift: ["comparable", "Fake evidence: comparable numbers."],
    representationShift: ["none-or-nonmaterial", "Fake evidence: same representation."],
    modeCompliance: ["compliant", "Fake evidence: mode rules obeyed."],
    graphConsistency: [graphCategory, "Fake evidence: graph matches its problem."],
    dataChange: ["changed", "Fake evidence: mathematical data changed."],
  };
  return Object.fromEntries(
    Object.entries(passing).map(([name, [category, evidence]]) => [
      name,
      { category, evidence },
    ]),
  );
}

function messageText(message) {
  if (!message) return "";
  if (typeof message.content === "string") return message.content;
  if (Array.isArray(message.content)) {
    return message.content.map((part) => part.text || "").join("\n");
  }
  return "";
}

function handleVariantRole(res, type, body) {
  const messages = body.messages || [];
  const systemMessage = messages.find((m) => m.role === "system") || {};
  const userMessage = messages.find((m) => m.role === "user") || {};
  const systemPrompt = messageText(systemMessage);
  const userPrompt = messageText(userMessage);
  const scenario = state.variants[type];
  state.variantCounts[type] += 1;
  const task = parseTaskData(userPrompt) || {};

  const failTransport = () =>
    sendJson(res, 503, {
      error: { message: `Fake ${type} failure`, type: "fake_error" },
    });
  const invalidJson = () => sendJson(res, 200, createCompletion("not valid json"));

  if (type === "generator") {
    if (scenario === "fail") return failTransport();
    if (scenario === "invalid") return invalidJson();
    const source = task.source || {};
    // data-only-safe data change: bump every number in the statement.
    const text = String(source.text || "").replace(/\d+/g, (n) => String(Number(n) + 1));
    const payload = {
      text,
      problemType: source.problemType || "short-answer",
      graphDsl: source.graphDsl ?? null,
      correctAnswer: source.correctAnswer || "4",
      providerMetadata: {},
    };
    return sendJson(res, 200, createCompletion(JSON.stringify(payload)));
  }

  if (type === "helper") {
    if (scenario === "fail") return failTransport();
    const variantResult =
      scenario === "different" ? "different" : scenario === "uncertain" ? "uncertain" : "equivalent";
    const payload = {
      original: { result: "equivalent", evidence: "Fake evidence: original answers match." },
      variant: { result: variantResult, evidence: `Fake evidence: variant comparison ${variantResult}.` },
      providerMetadata: {},
    };
    return sendJson(res, 200, createCompletion(JSON.stringify(payload)));
  }

  // variant-validator: scenario keys match the profile model names.
  const finish = () => {
    if (scenario === "fail") return failTransport();
    if (scenario === "invalid") return invalidJson();
    const payload = {
      originalSolvedAnswer: scenario === "unsolvable" ? null : "4",
      variantSolvedAnswer: scenario === "unsolvable" ? null : "4",
      originalSolutionSummary: "Fake original solution summary.",
      variantSolutionSummary: "Fake variant solution summary.",
      checks: validatorReport(task),
      providerMetadata: {},
    };
    return sendJson(res, 200, createCompletion(JSON.stringify(payload)));
  };
  if (scenario === "slow") {
    // Keep the validation phase in flight so tests can reload mid-generation.
    setTimeout(finish, 3000);
    return;
  }
  finish();
}

function consumeOverride(type) {
  const overrides = state.overrides[type];
  if (!overrides || overrides.length === 0) return undefined;
  const next = overrides[0];
  next.remaining -= 1;
  if (next.remaining <= 0) {
    overrides.shift();
  }
  return next;
}

function handleControl(req, res) {
  if (req.method === "GET") {
    sendJson(res, 200, fakeState());
    return;
  }
  if (req.method !== "POST") {
    sendJson(res, 405, { error: "Method not allowed" });
    return;
  }
  parseJson(req)
    .then((body) => {
      if (body.boxes !== undefined) {
        state.boxes = body.boxes;
      }
      if (body.mode && body.type) {
        if (!state.overrides[body.type]) {
          state.overrides[body.type] = [];
        }
        state.overrides[body.type].push({
          mode: body.mode,
          remaining: body.remaining ?? 1,
        });
      }
      if (body.variants) {
        for (const [role, scenario] of Object.entries(body.variants)) {
          if (role in state.variants) state.variants[role] = scenario;
        }
      }
      if (body.clear) {
        state.overrides = {};
        state.boxes = null;
        resetVariantState();
      }
      sendJson(res, 200, fakeState());
    })
    .catch((err) => sendJson(res, 400, { error: err.message }));
}

function handleChatCompletion(req, res) {
  if (req.method !== "POST") {
    sendJson(res, 405, { error: "Method not allowed" });
    return;
  }
  parseJson(req)
    .then((body) => {
      const messages = body.messages || [];
      const systemMessage = messages.find((m) => m.role === "system") || {};
      const userMessage = messages.find((m) => m.role === "user") || {};
      const systemPrompt =
        typeof systemMessage.content === "string"
          ? systemMessage.content
          : "";
      const userPrompt =
        typeof userMessage.content === "string" ? userMessage.content : "";
      const type = classifyRequestType(systemPrompt, userPrompt);

      const override = consumeOverride(type);
      if (override?.mode === "fail") {
        sendJson(res, 503, {
          error: {
            message: `Fake ${type} failure`,
            type: "fake_error",
          },
        });
        return;
      }
      if (override?.mode === "invalid") {
        sendJson(res, 200, createCompletion("not valid json"));
        return;
      }

      if (type === "variant-generator" || type === "variant-validator" || type === "variant-helper") {
        const role =
          type === "variant-validator"
            ? String(body.model || "").includes("validator-2")
              ? "validator-2"
              : "validator-1"
            : type.replace("variant-", "");
        handleVariantRole(res, role, body);
        return;
      }

      switch (type) {
        case "detection": {
          const boxes = state.boxes ?? defaultBoxes(body.messages);
          const payload = {
            subject: "math",
            boxes,
            providerMetadata: {},
          };
          sendJson(res, 200, createCompletion(JSON.stringify(payload)));
          break;
        }
        case "classification": {
          const payload = {
            subject: "math",
            confidence: 0.95,
            reason: "Fake classification sees math symbols.",
            providerMetadata: {},
          };
          sendJson(res, 200, createCompletion(JSON.stringify(payload)));
          break;
        }
        case "grading": {
          // Grading must be text-only: assert the source crop never leaks in.
          state.grading.count += 1;
          const hadImage = messages.some(
            (m) =>
              Array.isArray(m.content) &&
              m.content.some((part) => part.type === "image_url"),
          );
          if (hadImage) state.grading.hadImage = true;
          const payload = {
            isCorrect: true,
            feedback: "Fake feedback: correct.",
            providerMetadata: {},
          };
          sendJson(res, 200, createCompletion(JSON.stringify(payload)));
          break;
        }
        case "solution": {
          const payload = {
            steps_markdown: "Fake solution steps.",
            final_answer: "4",
            level_classification: "primary",
            providerMetadata: {},
          };
          sendJson(res, 200, createCompletion(JSON.stringify(payload)));
          break;
        }
        case "coaching": {
          const payload = {
            text: "Fake coaching reply.",
            whiteboard_dsl: null,
            providerMetadata: {},
          };
          sendJson(res, 200, createCompletion(JSON.stringify(payload)));
          break;
        }
        case "extraction":
        default: {
          const payload = {
            text: "What is 2 + 2?",
            problemType: "short-answer",
            graphDsl: null,
            providerMetadata: {},
          };
          sendJson(res, 200, createCompletion(JSON.stringify(payload)));
          break;
        }
      }
    })
    .catch((err) => sendJson(res, 400, { error: err.message }));
}

const server = http.createServer((req, res) => {
  if (req.url === "/health") {
    sendJson(res, 200, { status: "ok" });
    return;
  }
  if (req.url === "/_control") {
    handleControl(req, res);
    return;
  }
  if (req.url === "/chat/completions") {
    handleChatCompletion(req, res);
    return;
  }
  sendJson(res, 404, { error: "Not found" });
});

server.listen(PORT, () => {
  // eslint-disable-next-line no-console
  console.log(`Fake VLM server listening on port ${PORT}`);
});
