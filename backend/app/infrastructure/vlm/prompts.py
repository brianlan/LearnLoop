EXTRACTION_PROMPT_VERSION = "2026-05-29.extraction.v3"
GRADING_PROMPT_VERSION = "2026-05-14.grading.v1"

EXTRACTION_SCHEMA_VERSION = "1.0"
GRADING_SCHEMA_VERSION = "1.0"

EXTRACTION_PROMPT_TEMPLATE = """You are extracting a study problem from an image.
Return only JSON that matches the expected schema.

Fields:
- text: the problem statement as plain text
- problemType: one of single-choice, multi-choice, fill-in-the-blank, short-answer
- graphDsl: nullable string — if the image contains a geometric figure, graph, or diagram,
  write JavaScript code for the JSXGraph library to reconstruct it. Otherwise return null.

Text formatting:
- Preserve math notation with LaTeX delimiters when visible in the source image.
- Use `$...$` for inline math and `$$...$$` for display math.
- Put whitespace around inline `$...$` when it is adjacent to words, numbers, or other
  non-punctuation text. For example, write `Find $x$ when $x+1=2$`, not `Find$x$when$x+1=2$`.

## JSXGraph DSL rules

A `board` variable already exists (JXG board with axes and grid). Your code will be executed
as `new Function('board', dsl)(board)`. Use only `board.create(type, parents, options)` calls.

Available element types and their parents:
- point:   board.create('point', [x, y], {name:'A'})
- segment: board.create('segment', [p1, p2])
- line:    board.create('line', [p1, p2])
- arrow:   board.create('arrow', [p1, p2])            — for rays
- circle:  board.create('circle', [center, radius])    — radius can be a number or a point
- angle:   board.create('angle', [p3, vertex, p1], {radius:1, fillColor:'#ff000050'})
- polygon: board.create('polygon', [p1, p2, p3], {fillColor:'#cccccc'})
- text:    board.create('text', [x, y, 'label'])
- glider:  board.create('glider', [x, y, lineOrCircle], {name:'G'})
- intersection: board.create('intersection', [line1, line2, 0])
- midpoint:     board.create('midpoint', [p1, p2])
- perpendicular: board.create('perpendicular', [point, line])
- functiongraph: board.create('functiongraph', [f, xMin, xMax])

Guidelines:
- Set board.setBoundingBox([xMin, yMax, xMax, yMin]) first if the default [-5,5,5,-5] is unsuitable.
- Keep the construction simple: only reproduce what is visually present in the image.
- Do NOT call JXG.JSXGraph.initBoard — the board already exists.
- Output ONLY the JavaScript code, no comments, no markdown fences, no explanation.
- If the image has no geometric figure, graph, or diagram, return null for graphDsl.

## Example output for a triangle with an angle:
board.setBoundingBox([-1, 5, 5, -1]);
var A = board.create('point', [0, 0], {name:'A'});
var B = board.create('point', [4, 0], {name:'B'});
var C = board.create('point', [3, 3], {name:'C'});
board.create('segment', [A, B]);
board.create('segment', [B, C]);
board.create('segment', [C, A]);
board.create('angle', [B, A, C], {radius:0.8, fillColor:'#ff000030', name:'α'});
"""

GRADING_PROMPT_TEMPLATE = """You are grading a short-answer response against a stored answer key.
Return only JSON that matches the expected schema.

Fields:
- isCorrect: boolean
- feedback: concise explanation for the learner
"""
