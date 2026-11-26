import { humaniseBackend, transportPresentation, type BackendCatalog } from "@waypoint/web-core/backends";
import type { Backend, SessionTransport } from "@waypoint/web-core/types";

import type { ChoiceOption } from "@/components/ui/Choice";

export function agentOptions(agents: Backend[], catalog: BackendCatalog): ChoiceOption[] {
  return agents.map((id) => ({ value: id, label: humaniseBackend(id, catalog) }));
}

export function transportOptions(transports: SessionTransport[], catalog: BackendCatalog): ChoiceOption[] {
  return transports.map((value) => {
    const shown = transportPresentation(value, catalog);
    return { value, label: shown.name, detail: shown.description };
  });
}
