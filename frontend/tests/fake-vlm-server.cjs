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
};

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
      if (body.clear) {
        state.overrides = {};
      }
      sendJson(res, 200, { status: "ok", boxes: state.boxes, overrides: state.overrides });
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
  if (req.url === "/chat/completions" || req.url === "/v1/chat/completions") {
    handleChatCompletion(req, res);
    return;
  }
  sendJson(res, 404, { error: "Not found" });
});

server.listen(PORT, () => {
  // eslint-disable-next-line no-console
  console.log(`Fake VLM server listening on port ${PORT}`);
});
