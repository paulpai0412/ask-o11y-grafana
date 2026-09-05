declare module "@grafana/data" {
  import type React from "react";

  export interface PanelOptionsBuilder<TOptions> {
    addTextInput(option: {
      path: keyof TOptions;
      name: string;
    }): PanelOptionsBuilder<TOptions>;
  }

  export class PanelPlugin<TOptions> {
    constructor(
      component: React.ComponentType<{
        options: TOptions;
        width: number;
        height: number;
      }>,
    );
    setPanelOptions(
      configure: (
        builder: PanelOptionsBuilder<TOptions>,
      ) => PanelOptionsBuilder<TOptions>,
    ): this;
  }
}

declare module "@grafana/ui" {
  export function useTheme2(): {
    colors: {
      text: { primary: string };
      border: { weak: string };
      background: { primary: string };
    };
    typography: { fontFamily: string };
  };
}

declare module "plotly.js-dist-min" {
  type FigureData = Array<Record<string, unknown>>;
  type FigureLayout = Record<string, unknown>;
  type FigureConfig = Record<string, unknown>;
  const Plotly: {
    react(
      element: HTMLElement,
      data: FigureData,
      layout: FigureLayout,
      config: FigureConfig,
    ): Promise<unknown>;
    purge(element: HTMLElement): void;
    Plots: { resize(element: HTMLElement): void };
  };
  export default Plotly;
}
