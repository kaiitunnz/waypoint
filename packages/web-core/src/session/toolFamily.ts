// What kind of tool a name is, and the family whose colour its glyph takes.

import { isAgentToolName, isFileEditToolName } from "./toolPresentation";

export type ToolCategory =
  | "read"
  | "search"
  | "edit"
  | "bash"
  | "web"
  | "agent"
  | "followup"
  | "skill"
  | "monitor"
  | "todo"
  | "other";

export function categoryOf(tool: string | null): ToolCategory {
  switch (tool) {
    case "Read":
      return "read";
    case "Grep":
    case "Glob":
      return "search";
    case "Bash":
      return "bash";
    case "WebFetch":
    case "WebSearch":
      return "web";
    case "Skill":
      return "skill";
    case "Monitor":
      return "monitor";
    case "TodoWrite":
      return "todo";
    // Waiting on or closing a subagent is part of one subagent's life, not another one.
    case "Wait":
    case "closeAgent":
      return "followup";
    default:
      if (isFileEditToolName(tool)) return "edit";
      if (isAgentToolName(tool)) return "agent";
      return "other";
  }
}

// The families that share a glyph colour; every other tool's glyph stays plain.
export type ToolFamily = "bash" | "read" | "edit" | "agent" | "skill";

const FAMILIES: Partial<Record<ToolCategory, ToolFamily>> = {
  bash: "bash",
  // A monitor watches a command's output.
  monitor: "bash",
  read: "read",
  search: "read",
  web: "read",
  edit: "edit",
  agent: "agent",
  followup: "agent",
  todo: "agent",
  skill: "skill",
};

export const familyOf = (category: ToolCategory): ToolFamily | null => FAMILIES[category] ?? null;

export const toolFamilyOf = (tool: string | null): ToolFamily | null => familyOf(categoryOf(tool));
