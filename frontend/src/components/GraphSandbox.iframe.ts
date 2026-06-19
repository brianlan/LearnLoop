// Pinned JSXGraph version for security and stability
export const JSXGRAPH_VERSION = "1.10.0";
export const JSXGRAPH_CDN_URL = `https://cdn.jsdelivr.net/npm/jsxgraph@${JSXGRAPH_VERSION}/distrib/jsxgraphcore.js`;

/**
 * Generates the iframe HTML content with JSXGraph loader.
 * This is loaded into the sandboxed iframe.
 * Exported for testing only.
 */
export function generateIframeHtml(): string {
  // CSP justification:
  // - default-src 'none': block everything by default
  // - script-src:
  //   - 'unsafe-eval': required for new Function('board', dsl) to execute the DSL
  //   - 'unsafe-inline': required for the inline <script> tag that sets up the sandbox
  //   - ${JSXGRAPH_CDN_URL}: only allow JSXGraph from our pinned CDN version
  // - style-src 'unsafe-inline': required for inline JSXGraph styles
  // - img-src data:: allow data URIs for JSXGraph images
  // - All other directives set to 'none' to block unnecessary capabilities
  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="
    default-src 'none';
    script-src 'unsafe-eval' 'unsafe-inline' ${JSXGRAPH_CDN_URL};
    style-src 'unsafe-inline';
    img-src data:;
    connect-src 'none';
    font-src 'none';
    frame-src 'none';
    object-src 'none';
    media-src 'none';
    base-uri 'none';
    form-action 'none';
  ">
  <title>JSXGraph Sandbox</title>
  <script src="${JSXGRAPH_CDN_URL}"></script>
  <style>
    html, body {
      margin: 0;
      padding: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      font-family: sans-serif;
    }
    #jxgbox {
      width: 100%;
      height: 100%;
    }
    .error {
      color: #dc2626;
      padding: 16px;
      font-size: 14px;
    }
  </style>
</head>
<body>
  <div id="jxgbox"></div>
  <script>
    (function() {
      // Track board instance for cleanup
      let board = null;

      // Message handler for postMessage protocol
      function handleMessage(event) {
        const data = event.data;
        
        if (!data || typeof data !== 'object') {
          return;
        }

        switch (data.type) {
          case 'render':
            handleRender(data.payload);
            break;
          case 'clear':
            handleClear();
            break;
          default:
            console.warn('Unknown message type:', data.type);
        }
      }

      function handleRender(dsl) {
        try {
          // Clear any existing board
          if (board) {
            JXG.JSXGraph.freeBoard(board);
            board = null;
          }

          // Create new board
          board = JXG.JSXGraph.initBoard('jxgbox', {
            boundingbox: [-5, 5, 5, -5],
            axis: false,
            grid: false,
            showCopyright: false,
            showNavigation: true,
            keepaspectratio: true
          });

          // Execute the DSL in a controlled way
          // The DSL is expected to be a function that takes the board as parameter
          const dslFunction = new Function('board', dsl);
          dslFunction(board);

          // Notify parent of success
          parent.postMessage({ type: 'rendered' }, '*');
        } catch (error) {
          // Notify parent of error
          parent.postMessage({ 
            type: 'error', 
            payload: error instanceof Error ? error.message : String(error)
          }, '*');
        }
      }

      function handleClear() {
        if (board) {
          JXG.JSXGraph.freeBoard(board);
          board = null;
        }
        // Notify parent of clear completion
        parent.postMessage({ type: 'cleared' }, '*');
      }

      // Listen for messages from parent
      window.addEventListener('message', handleMessage);

      // Notify parent that sandbox is ready
      parent.postMessage({ type: 'ready' }, '*');

      // Cleanup on page unload
      window.addEventListener('beforeunload', function() {
        if (board) {
          JXG.JSXGraph.freeBoard(board);
        }
      });
    })();
  </script>
</body>
</html>`;
}
