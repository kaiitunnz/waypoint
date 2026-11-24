export type FrontendVariant = "v1" | "v2";

export function variantLabel(variant: FrontendVariant): string {
  return `Waypoint ${variant}`;
}
