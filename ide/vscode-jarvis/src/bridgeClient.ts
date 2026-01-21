import * as http from "http";
import * as https from "https";
import { URL } from "url";

export type BridgeClientOptions = {
  baseUrl: string;
  token: string;
};

function requestJson<T>(method: string, urlStr: string, token: string, body?: any): Promise<T> {
  const url = new URL(urlStr);
  const isHttps = url.protocol === "https:";
  const lib = isHttps ? https : http;

  const payload = body ? Buffer.from(JSON.stringify(body), "utf8") : undefined;

  const options: http.RequestOptions = {
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
            resolve(data ? JSON.parse(data) : ({} as any));
          } catch {
            // some endpoints return plain text
            resolve((data as any) as T);
          }
        } else {
          reject(new Error(`${res.statusCode} ${res.statusMessage}: ${data}`));
        }
      });
    });

    req.on("error", reject);
    if (payload) req.write(payload);
    req.end();
  });
}

export class BridgeClient {
  private baseUrl: string;
  private token: string;

  constructor(opts: BridgeClientOptions) {
    this.baseUrl = opts.baseUrl.replace(/\/+$/, "");
    this.token = opts.token;
  }

  health(): Promise<any> {
    return requestJson<any>("GET", `${this.baseUrl}/health`, this.token);
  }

  startSession(workspaceRoot: string, client: string, preferences: any): Promise<any> {
    return requestJson<any>("POST", `${this.baseUrl}/v1/session/start`, this.token, {
      workspace_root: workspaceRoot,
      client,
      preferences
    });
  }

  setContext(sessionId: string, ctx: any): Promise<any> {
    return requestJson<any>("POST", `${this.baseUrl}/v1/session/${encodeURIComponent(sessionId)}/context`, this.token, ctx);
  }

  setDiagnostics(sessionId: string, diagnostics: any[]): Promise<any> {
    return requestJson<any>("POST", `${this.baseUrl}/v1/session/${encodeURIComponent(sessionId)}/diagnostics`, this.token, { diagnostics });
  }

  request(sessionId: string, prompt: string, options: any): Promise<any> {
    return requestJson<any>("POST", `${this.baseUrl}/v1/session/${encodeURIComponent(sessionId)}/request`, this.token, { prompt, options });
  }

  job(jobId: string): Promise<any> {
    return requestJson<any>("GET", `${this.baseUrl}/v1/job/${encodeURIComponent(jobId)}`, this.token);
  }

  status(sessionId: string): Promise<any> {
    return requestJson<any>("GET", `${this.baseUrl}/v1/session/${encodeURIComponent(sessionId)}/status`, this.token);
  }

  apply(sessionId: string, confirm: string): Promise<any> {
    return requestJson<any>("POST", `${this.baseUrl}/v1/session/${encodeURIComponent(sessionId)}/apply`, this.token, { confirm });
  }

  discard(sessionId: string): Promise<any> {
    return requestJson<any>("POST", `${this.baseUrl}/v1/session/${encodeURIComponent(sessionId)}/discard`, this.token, {});
  }
}
