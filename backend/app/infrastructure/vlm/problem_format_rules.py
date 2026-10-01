"""Shared problem-formatting rules for the VLM extraction and variant
generator prompts (issue #649).

`MATH_EXTRACTION_SYSTEM_PROMPT` and `VARIANT_GENERATOR_SYSTEM_PROMPT` both
interpolate these constants, so the formatting standard lives in exactly one
place. The text is deliberately image-neutral: it must read correctly for
both roles. Extraction-only framing (faithful extraction, no-solve cleanup,
problem-type classification) stays in the extraction prompt itself.
"""

PROBLEM_TEXT_FORMAT_RULES = r"""## Text formatting principles

Use minimal LaTeX:
- Do not put ordinary numbers in LaTeX.
  Good: `45`, `3`, `100`
  Bad: `$45$`, `$3$`, `$100$`
- Do not put ordinary choice labels in LaTeX.
  Good:
  `A. 12`
  `B. 15`
  Bad:
  `$A$. 12`
  `$B$. 15`
- Do not put ordinary standalone letters in LaTeX when they are simple labels or option names.
  Good: `点 A`, `图中 A、B、C 三点`
  Bad: `点 $A$`, `图中 $A$、$B$、$C$ 三点`
- Use LaTeX only for actual mathematical notation that benefits from mathematical formatting, such as:
  equations, inequalities, algebraic expressions, fractions, roots, exponents, subscripts,
  ratios, functions, coordinates, vectors, angle notation, geometric relations, and units inside formulas.
  Good: `$x+1=2$`, `$\frac{1}{2}$`, `$a^2+b^2=c^2$`, `$\angle ABC=45^\circ$`, `$(3,4)$`
- If a short expression mixes letters and mathematical operators, use LaTeX.
  Good: `$A-B$`, `$AB=CD$`, `$x>0$`
- If the content is ordinary prose, keep it as ordinary text.

## Inline LaTeX rules

Use `$...$` for inline math.
Use `$$...$$` for display math only if the problem clearly has a displayed formula or a large standalone formula.

Spacing around inline LaTeX:
- Put one ASCII space before and after every inline `$...$` unless it is at the beginning or end of a line.
- This also applies when `$...$` is adjacent to Chinese-style punctuation.
  Good: `已知 $x+1=2$ ，求 $x$ 的值。`
  Good: `如图， $AB=CD$ ，求 $\angle A$ 。`
  Good: `甲、乙两地相距 $A-B$ ，汽车行驶了 2 小时。`
  Bad: `已知$x+1=2$，求$x$的值。`
  Bad: `已知 $x+1=2$，求 $x$ 的值。`
  Bad: `如图，$AB=CD$，求$\angle A$。`
- Do not add extra spaces inside the LaTeX delimiters.
  Good: `$x+1=2$`
  Bad: `$ x+1=2 $`

Chinese punctuation:
- Preserve Chinese punctuation such as `，`, `。`, `、`, `；`, `：`, `？`, or `！` when visible or natural.
- When inline LaTeX touches such punctuation, keep one ASCII space between them.
  Good: `若 $a>b$ ，则 $a-c>b-c$ 。`
  Bad: `若$a>b$，则$a-c>b-c$。`

## Fill-in-the-blank rules

For blanks:
- If the problem contains a fill-in blank line, underscore run, or empty answer line, represent it as:
  `$\underline{\quad\quad\quad}$`
- Do not represent blanks using repeated underscores.
  Good: `答案是 $\underline{\quad\quad\quad}$ 。`
  Bad: `答案是 ________。`
  Bad: `答案是 ____ 。`
- For multiple blanks, use one underline expression per blank.
  Good: `$\underline{\quad\quad\quad}$ ， $\underline{\quad\quad\quad}$`
- If the blank is visibly very short or very long, still use the same normalized form unless the difference is semantically important.
- If the blank is part of a formula, keep the blank expression inside the surrounding math only if needed.
  Good: `$x=\underline{\quad\quad\quad}$`
  Good: `答案是 $\underline{\quad\quad\quad}$ 。`

Important JSON escaping rule:
- The final answer must be valid JSON.
- Because JSON strings require backslashes to be escaped, LaTeX backslashes in the actual JSON output must appear as `\\`.
- For example, the JSON string value should contain:
  `$\\underline{\\quad\\quad\\quad}$`
  not:
  `$\underline{\quad\quad\quad}$`

## Choices and line breaks

For single-choice and multi-choice problems:
- Put each option on its own line.
- Use plain choice labels: `A.`, `B.`, `C.`, `D.`
- Do not wrap option labels in LaTeX.
- Preserve the option content after the label.
- If an option contains mathematical notation, only wrap the mathematical expression, not the label.

Good:
`A. 12`
`B. $x+1$`
`C. $\frac{1}{2}$`
`D. 无法确定`

Bad:
`$A$. 12`
`A. $12$`
`$B$. $x+1$`"""

GRAPH_DSL_AUTHORING_RULES = r"""## JSXGraph DSL purpose

Use `graphDsl` only when a visual figure is needed to solve or understand the problem.

Good candidates:
- plane geometry diagrams
- coordinate graphs
- points, line segments, rays, circles, polygons, angles
- simple geometric constructions

Do not use `graphDsl` for:
- decorative pictures
- photos
- cartoons
- ordinary tables
- irrelevant illustrations

If a non-geometric visual is needed, describe the relevant visible information briefly in `text` and return null for `graphDsl`.

## JSXGraph execution environment

A `board` variable already exists. It is a JXG board with axes and grid.
The `graphDsl` JSON field will be executed as:

`new Function('board', graphDsl)(board)`

Use only:
- `board.setBoundingBox(...)`
- variable declarations
- `board.create(type, parents, options)` calls

Do not use any other JavaScript APIs.

The `graphDsl` field must be a valid JSON string.
Escape newlines and quotes as required by JSON.

## Available JSXGraph element types

Allowed element types and their parents:
- point:          `board.create('point', [x, y], {name:'A'})`
- segment:        `board.create('segment', [p1, p2])`
- line:           `board.create('line', [p1, p2])`
- arrow:          `board.create('arrow', [p1, p2])`
- circle:         `board.create('circle', [center, radius])`
- angle:          `board.create('angle', [p3, vertex, p1], {radius:1, fillColor:'#ff000050'})`
- polygon:        `board.create('polygon', [p1, p2, p3], {fillColor:'#cccccc30'})`
- text:           `board.create('text', [x, y, 'label'])`
- glider:         `board.create('glider', [x, y, lineOrCircle], {name:'G'})`
- intersection:   `board.create('intersection', [line1, line2, 0], {name:'O'})`
- midpoint:       `board.create('midpoint', [p1, p2], {name:'M'})`
- perpendicular:  `board.create('perpendicular', [point, line])`

Common useful options:
- Named visible point: `{name:'A'}`
- Unnamed visible point: `{name:'', withLabel:false}`
- Hidden helper point: `{name:'', withLabel:false, visible:false}`
- Dashed segment: `{dash:2}`
- Thin helper segment: `{strokeWidth:1}`
- Shaded polygon: `{fillColor:'#cccccc50', borders:{strokeColor:'#000000'}}`
- Text label: `board.create('text', [x, y, '5 cm'])`

## Geometry diagram construction workflow

Before writing `graphDsl`, internally analyze the diagram in this order.
Do not include this analysis in the output.

### 1. Identify all points first

Find every point needed to reconstruct the diagram.

Classify points into:
- named visible points, such as A, B, C, D, O
- unnamed visible endpoints, if the source visibly marks a point but gives no name
- hidden helper endpoints, needed only to place a segment, ray, circle, or polygon
- intersection-generated points, such as the intersection of two diagonals or a line meeting a side

Create named visible points first.
If a point has a visible name label in the source, use that name.

Examples:
`var A = board.create('point', [0, 0], {name:'A'});`
`var B = board.create('point', [4, 0], {name:'B'});`

For helper points that are not visibly marked in the source, hide the point:
`var P = board.create('point', [2, 3], {name:'', withLabel:false, visible:false});`

Do not add visible point labels that the problem's data does not call for.

### 2. Choose a simple coordinate layout

Use approximate coordinates that preserve the source diagram's visual topology:
- relative left/right/up/down positions
- which points are connected
- which lines intersect
- which regions are shaded
- approximate proportions and orientation

Do not try to solve the geometry.
Do not force exact lengths or angles unless they are explicitly visible and easy to preserve.
For non-scale geometry diagrams, visual similarity and correct connectivity are more important than mathematical precision.

Set `board.setBoundingBox([xMin, yMax, xMax, yMin], true)` first if the default `[-5,5,5,-5]` is unsuitable.
Keep the figure comfortably inside the bounding box and avoid placing labels on the border.

### 3. Draw visible segments next

Use `segment` by default.

Most geometry diagram lines are finite segments, not infinite lines.
Use `line` only if the source clearly shows a full infinite line or the problem explicitly refers to a line extending indefinitely.
Use `arrow` only for a ray or arrow visibly shown in the source.

Good:
`board.create('segment', [A, B]);`

Bad for ordinary triangle sides:
`board.create('line', [A, B]);`

If a segment is dashed in the source, preserve it as dashed:
`board.create('segment', [A, D], {dash:2});`

Do not add construction lines, diagonals, or extensions that the problem's data does not call for.

### 4. Create named intersection points

After the relevant segments or lines exist, create visible named intersection points.

If using `intersection` is simple and reliable, use it.
If using `intersection` would require converting ordinary finite segments into infinite `line` objects, prefer directly placing an approximate named point at the visual intersection.

Acceptable direct point:
`var O = board.create('point', [2, 1.5], {name:'O'});`

Use direct approximate points when visual reconstruction is more reliable than symbolic construction.
Do not sacrifice visual correctness just to use `intersection`.

### 5. Draw shaded regions

If the source contains shading, draw the shaded region with `polygon` after its boundary points exist.

Use transparent fill colors.
Do not cover the whole diagram with an opaque polygon.
Do not invent shaded regions.

Example:
`board.create('polygon', [A, B, C], {fillColor:'#cccccc50'});`

If the shaded region has a visible boundary, the boundary should also be represented by the existing segments or polygon border.

### 6. Add circles, arcs, angles, and annotations

After points and main segments are in place, add remaining visible elements:
- circles
- angle markers
- right-angle markers if possible
- length labels such as `5 cm`
- angle labels such as `40°`
- text labels
- coordinate labels
- arrows or rays
- other visible annotations

Use `angle` for visible angle marks:
`board.create('angle', [B, A, C], {radius:0.8, fillColor:'#ff000030'});`

Use `text` for numeric labels or annotations:
`board.create('text', [2, -0.3, '5 cm']);`

Only add annotations that the problem's data calls for.

## Coordinate graph guidelines

For coordinate graphs:
- Preserve axes, grid, plotted points, curves, segments, and labels needed to solve the problem.
- Use the existing board axes and grid when suitable.
- Set a bounding box that matches the visible coordinate range.
- Plot visible points with their labels if labels are shown.
- Use `segment` for finite graph segments and `line` only for full lines.
- If the graph shows a curve that cannot be represented by the allowed element types, approximate only the essential visible information or return null for `graphDsl` if reconstruction would be misleading.

## Graph drawing constraints

- Keep the construction simple.
- Only draw the elements the problem's data calls for.
- Do not call `JXG.JSXGraph.initBoard`; the board already exists.
- In `graphDsl`, output only allowed JavaScript statements.
- Do not use comments, markdown fences, explanatory prose, loops, conditionals, functions,
  arithmetic expressions, browser globals, or calls other than `board.setBoundingBox` and `board.create`.
- Do not use custom JavaScript helper functions.
- Do not use arrays of points except as direct parents for allowed `board.create` calls.
- If the problem has no suitable geometric figure, coordinate graph, or diagram, return null for `graphDsl`.

## Example graphDsl content for a triangle with a dashed height and shaded region

`board.setBoundingBox([-1, 5, 5, -1], true);
var A = board.create('point', [0, 0], {name:'A'});
var B = board.create('point', [4, 0], {name:'B'});
var C = board.create('point', [2.5, 3], {name:'C'});
var D = board.create('point', [2.5, 0], {name:'D'});
board.create('polygon', [A, D, C], {fillColor:'#cccccc50'});
board.create('segment', [A, B]);
board.create('segment', [B, C]);
board.create('segment', [C, A]);
board.create('segment', [C, D], {dash:2});
board.create('angle', [B, A, C], {radius:0.8, fillColor:'#ff000030'});
board.create('text', [2, -0.3, '6 cm']);`"""
