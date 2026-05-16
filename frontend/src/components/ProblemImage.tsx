import { useEffect, useState } from "react";
import type { CSSProperties } from "react";

interface ProblemImageProps {
  src?: string | null;
  alt?: string;
  style?: CSSProperties;
}

export function ProblemImage({ src, alt = "Problem", style }: ProblemImageProps) {
  const [isBroken, setIsBroken] = useState(false);

  useEffect(() => {
    setIsBroken(false);
  }, [src]);

  if (!src || isBroken) {
    return null;
  }

  return <img src={src} alt={alt} style={style} onError={() => setIsBroken(true)} />;
}
