import type { NextConfig } from "next";
import { networkInterfaces } from "node:os";

function collectAllowedDevOrigins(): string[] {
  const devPort = process.env.PORT ?? process.env.NEXT_DEV_PORT ?? "3000";
  const origins = new Set<string>();
  const extraOrigins = process.env.NEXT_ALLOWED_DEV_ORIGINS
    ?.split(",")
    .map((value) => value.trim())
    .filter(Boolean);

  addOriginVariants(origins, "localhost", devPort);
  addOriginVariants(origins, "127.0.0.1", devPort);

  if (extraOrigins) {
    for (const origin of extraOrigins) {
      origins.add(origin);
    }
  }

  for (const interfaces of Object.values(networkInterfaces())) {
    for (const network of interfaces ?? []) {
      if (network.internal || network.family !== "IPv4") {
        continue;
      }
      addOriginVariants(origins, network.address, devPort);
    }
  }

  return [...origins];
}

function addOriginVariants(origins: Set<string>, host: string, port: string): void {
  origins.add(host);
  origins.add(`http://${host}:${port}`);
  origins.add(`https://${host}:${port}`);
}

const nextConfig: NextConfig = {
  allowedDevOrigins: collectAllowedDevOrigins(),
  reactStrictMode: true,
};

export default nextConfig;
