// Shared resolve aliases for Vite dev server and Vitest.
// node_modules live at /app/node_modules (Docker named volume),
// not under /src (read-only mount). Both configs must import this.
export const nodeModulesPath = '/app/node_modules';

export const sharedResolve = {
  alias: {
    'react': `${nodeModulesPath}/react`,
    'react-dom': `${nodeModulesPath}/react-dom`,
    'react-router-dom': `${nodeModulesPath}/react-router-dom`,
    'highlight.js': `${nodeModulesPath}/highlight.js`,
    'yaml': `${nodeModulesPath}/yaml`,
  },
};
