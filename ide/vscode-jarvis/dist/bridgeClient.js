"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.BridgeClient = void 0;
const http = require("http");
const https = require("https");
const url_1 = require("url");
function requestJson(method, urlStr, token, body) {
    const url = new url_1.URL(urlStr);
    const isHttps = url.protocol === "https:";
    const lib = isHttps ? https : http;
    const payload = body ? Buffer.from(JSON.stringify(body), "utf8") : undefined;
    const options = {
        method,
        hostname: url.hostname,
        port: url.port,
        path: url.pathname + url.search,
        headers: {
            "Authorization": `Bearer ${token}`,
            "Content-Type": "application/json",
            ...(payload ? { "Content-Length": payload.length } : {})
        }
    };
    return new Promise((resolve, reject) => {
        const req = lib.request(options, (res) => {
            let data = "";
            res.setEncoding("utf8");
            res.on("data", (chunk) => (data += chunk));
            res.on("end", () => {
                if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
                    try {
                        resolve(data ? JSON.parse(data) : {});
                    }
                    catch {
                        // some endpoints return plain text
                        resolve(data);
                    }
                }
                else {
                    reject(new Error(`${res.statusCode} ${res.statusMessage}: ${data}`));
                }
            });
        });
        req.on("error", reject);
        if (payload)
            req.write(payload);
        req.end();
    });
}
class BridgeClient {
    constructor(opts) {
        this.baseUrl = opts.baseUrl.replace(/\/+$/, "");
        this.token = opts.token;
    }
    health() {
        return requestJson("GET", `${this.baseUrl}/health`, this.token);
    }
    startSession(workspaceRoot, client, preferences) {
        return requestJson("POST", `${this.baseUrl}/v1/session/start`, this.token, {
            workspace_root: workspaceRoot,
            client,
            preferences
        });
    }
    setContext(sessionId, ctx) {
        return requestJson("POST", `${this.baseUrl}/v1/session/${encodeURIComponent(sessionId)}/context`, this.token, ctx);
    }
    setDiagnostics(sessionId, diagnostics) {
        return requestJson("POST", `${this.baseUrl}/v1/session/${encodeURIComponent(sessionId)}/diagnostics`, this.token, { diagnostics });
    }
    request(sessionId, prompt, options) {
        return requestJson("POST", `${this.baseUrl}/v1/session/${encodeURIComponent(sessionId)}/request`, this.token, { prompt, options });
    }
    job(jobId) {
        return requestJson("GET", `${this.baseUrl}/v1/job/${encodeURIComponent(jobId)}`, this.token);
    }
    status(sessionId) {
        return requestJson("GET", `${this.baseUrl}/v1/session/${encodeURIComponent(sessionId)}/status`, this.token);
    }
    apply(sessionId, confirm) {
        return requestJson("POST", `${this.baseUrl}/v1/session/${encodeURIComponent(sessionId)}/apply`, this.token, { confirm });
    }
    discard(sessionId) {
        return requestJson("POST", `${this.baseUrl}/v1/session/${encodeURIComponent(sessionId)}/discard`, this.token, {});
    }
}
exports.BridgeClient = BridgeClient;
//# sourceMappingURL=bridgeClient.js.map