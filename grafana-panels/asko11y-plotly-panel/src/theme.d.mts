export type GrafanaThemeShape = {
  colors: {
    text: { primary: string };
    border: { weak: string };
    background: { primary: string };
  };
  typography: { fontFamily: string };
};

export function applyGrafanaTheme(
  layout: Record<string, unknown>,
  theme: GrafanaThemeShape,
): Record<string, unknown>;
