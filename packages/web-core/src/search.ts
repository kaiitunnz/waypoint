export interface SearchTerm {
  field?: string;
  isRegex: boolean;
  value: string | RegExp;
}

export type SearchGroup = SearchTerm[]; // AND terms
export type ParsedQuery = SearchGroup[]; // OR groups

const ALIASES: Record<string, string> = {
  repo: "repo_name",
  dir: "cwd",
  directory: "cwd",
  path: "cwd",
  app: "backend",
  agent: "backend",
  state: "search_status",
  status: "search_status",
};

export function parseQuery(query: string): ParsedQuery {
  const groups: ParsedQuery = [];
  let currentGroup: SearchGroup = [];

  const regex = /(?:([a-zA-Z0-9_]+):)?(\/(?:\\\/|[^/])+\/[a-z]*|"[^"]*"|'[^']*'|[^\s]+)/g;
  
  let match;
  while ((match = regex.exec(query)) !== null) {
    let field = match[1]?.toLowerCase();
    if (field && ALIASES[field]) {
      field = ALIASES[field];
    }
    
    const rawValue = match[2];
    
    // Explicit OR / AND logic
    if (!field) {
      const upperVal = rawValue.toUpperCase();
      if (upperVal === "OR") {
        if (currentGroup.length > 0) {
          groups.push(currentGroup);
          currentGroup = [];
        }
        continue;
      }
      if (upperVal === "AND") {
        continue; // implicitly AND, so just skip
      }
    }

    let isRegex = false;
    let value: string | RegExp = rawValue;

    if (rawValue.startsWith('/') && rawValue.lastIndexOf('/') > 0) {
      const lastSlash = rawValue.lastIndexOf('/');
      const pattern = rawValue.slice(1, lastSlash);
      const flags = rawValue.slice(lastSlash + 1);
      try {
        const finalFlags = flags.includes('i') ? flags : flags + 'i';
        value = new RegExp(pattern, finalFlags);
        isRegex = true;
      } catch {
        // Fallback safely to a plain string if regex compilation fails
        value = rawValue.toLowerCase();
      }
    } else if (rawValue.startsWith('"') && rawValue.endsWith('"') && rawValue.length > 1) {
      value = rawValue.slice(1, -1).toLowerCase();
    } else if (rawValue.startsWith("'") && rawValue.endsWith("'") && rawValue.length > 1) {
      value = rawValue.slice(1, -1).toLowerCase();
    } else {
      value = rawValue.toLowerCase();
    }

    currentGroup.push({ field, isRegex, value });
  }
  
  if (currentGroup.length > 0) {
    groups.push(currentGroup);
  }
  return groups;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function matchesQuery(item: any, groups: ParsedQuery, defaultFields: string[]): boolean {
  if (groups.length === 0) return true;
  
  // Inject a virtual field for status searching so "active" can match any non-exited status.
  const searchItem = item.status
    ? { ...item, search_status: item.status !== "exited" ? `${item.status} active` : item.status }
    : item;
  
  for (const group of groups) {
    let groupMatched = true;
    for (const term of group) {
      const fieldsToSearch = term.field ? [term.field] : defaultFields;
      
      let termMatched = false;
      for (const field of fieldsToSearch) {
        const itemValue = searchItem[field];
        if (itemValue == null) continue;
        
        const strValue = String(itemValue);
        if (term.isRegex) {
          if ((term.value as RegExp).test(strValue)) {
            termMatched = true;
            break;
          }
        } else {
          if (strValue.toLowerCase().includes(term.value as string)) {
            termMatched = true;
            break;
          }
        }
      }
      
      if (!termMatched) {
        groupMatched = false;
        break;
      }
    }
    
    // If ANY group fully matched (OR logic), the item matches
    if (groupMatched) return true;
  }
  
  return false;
}
