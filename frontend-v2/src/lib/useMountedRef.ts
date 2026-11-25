"use client";

import { useEffect, useRef, type RefObject } from "react";

// False once the component is gone, which a request in flight can outlive.
export function useMountedRef(): RefObject<boolean> {
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  return mounted;
}
