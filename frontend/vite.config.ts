import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

type ProxyErrorResponse = {
  headersSent: boolean;
  writeHead(statusCode: number, headers: Record<string, string>): void;
  end(body: string): void;
};

type ProxyWithErrorHandler = {
  on(
    event: "error",
    handler: (error: Error, request: unknown, response: ProxyErrorResponse | undefined) => void,
  ): void;
};

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        configure: (proxy) => {
          const proxyWithErrors = proxy as unknown as ProxyWithErrorHandler;

          proxyWithErrors.on("error", (_error, _request, response) => {
            if (!response || response.headersSent) {
              return;
            }

            response.writeHead(503, { "Content-Type": "application/json" });
            response.end(
              JSON.stringify({
                error: "Backend is not running. Start the backend server on http://127.0.0.1:8000 and try again.",
              }),
            );
          });
        },
      },
    },
  },
});
