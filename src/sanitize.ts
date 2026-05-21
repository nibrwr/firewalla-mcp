const sensitiveKeys = new Set([
  "privateKey",
  "secretKey",
  "wifiKey",
  "encryption",
  "password",
  "secret",
  "psk",
  "token",
  "credential",
  "apiKey",
  "key",
  "passphrase",
  "preSharedKey",
]);

export function sanitizeNetworkConfig(config: any): any {
  if (config == null || typeof config !== "object") return config;

  if (Array.isArray(config)) {
    return config.map(sanitizeNetworkConfig);
  }

  const result: any = {};
  for (const [k, v] of Object.entries(config)) {
    if (sensitiveKeys.has(k) && typeof v === "string") {
      result[k] = "[REDACTED]";
    } else if (k === "profile" && typeof v === "object" && v !== null) {
      // WiFi SSID profiles contain passwords in "key" field
      const profiles: any = {};
      for (const [pid, profile] of Object.entries(v as Record<string, any>)) {
        const sanitizedProfile = sanitizeNetworkConfig(profile);
        if (
          sanitizedProfile &&
          typeof sanitizedProfile === "object" &&
          typeof sanitizedProfile.key === "string"
        ) {
          sanitizedProfile.key = "[REDACTED]";
        }
        profiles[pid] = sanitizedProfile;
      }
      result[k] = profiles;
    } else if (k === "extra" && typeof v === "object" && v !== null && (v as any).peers) {
      // WireGuard extra.peers contain privateKey
      const extra = { ...v as any };
      extra.peers = (extra.peers as any[]).map((peer: any) => {
        const { privateKey, ...rest } = peer;
        return { ...rest, ...(privateKey ? { privateKey: "[REDACTED]" } : {}) };
      });
      result[k] = extra;
    } else if (typeof v === "object") {
      result[k] = sanitizeNetworkConfig(v);
    } else {
      result[k] = v;
    }
  }
  return result;
}
