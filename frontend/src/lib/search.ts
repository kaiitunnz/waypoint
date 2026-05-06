export interface SearchTerm {
  field?: string;
  isRegex: boolean;
  value: string | RegExp;
}

const ALIASES: Record<string, string> = {
  repo: "repo_name",
  dir: "cwd",
  directory: "cwd",
  path: "cwd",
  app: "backend",
  agent: "backend",
};

export function parseQuery(query: string): SearchTerm[] {
  const terms: SearchTerm[] = [];
  // Match field:value, field:"value", field:/regex/, value, "value", /regex/
  // Breakdown:
  // (?:([a-zA-Z0-9_]+):)? -> Optional field name followed by a colon
  // ( ... ) -> The value portion
  // \/(?:\\\/|[^/])+\/[a-z]* -> Regex literal (e.g. /foo/i), supporting escaped slashes
  // "[^"]*" -> Double quoted string
  // '[^']*' -> Single quoted string
  // [^\s]+ -> Anything else (fallback to standard string)
  const regex = /(?:([a-zA-Z0-9_]+):)?(\/(?:\\\/|[^/])+\/[a-z]*|"[^"]*"|'[^']*'|[^\s]+)/g;
  
  let match;
  while ((match = regex.exec(query)) !== null) {
    let field = match[1]?.toLowerCase();
    if (field && ALIASES[field]) {
      field = ALIASES[field];
    }
    
    const rawValue = match[2];
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

    terms.push({ field, isRegex, value });
  }
  return terms;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function matchesTerms(item: any, terms: SearchTerm[], defaultFields: string[]): boolean {
  if (terms.length === 0) return true;
  
  for (const term of terms) {
    const fieldsToSearch = term.field ? [term.field] : defaultFields;
    
    let termMatched = false;
    for (const field of fieldsToSearch) {
      const itemValue = item[field];
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
    
    if (!termMatched) return false;
  }
  return true;
}
