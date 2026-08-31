import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig(function (_a) {
    var _b;
    var mode = _a.mode;
    var env = loadEnv(mode, process.cwd(), "");
    var port = Number((_b = env.FRONTEND_PORT) !== null && _b !== void 0 ? _b : "5175");
    return {
        plugins: [react()],
        server: { host: "127.0.0.1", port: port, strictPort: true },
    };
});
