import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";

import { ThemeProvider, useTheme } from "./ThemeContext";

// Helper component to test the context
function ThemeConsumer() {
  const { theme, setTheme, toggleTheme } = useTheme();

  return (
    <div>
      <span data-testid="theme-value">{theme}</span>
      <button data-testid="toggle-button" onClick={toggleTheme}>
        Toggle
      </button>
      <button data-testid="set-light-button" onClick={() => setTheme("light")}>
        Set Light
      </button>
      <button data-testid="set-dark-button" onClick={() => setTheme("dark")}>
        Set Dark
      </button>
    </div>
  );
}

function renderThemeProvider(children: React.ReactNode = <ThemeConsumer />) {
  return render(
    <ThemeProvider>
      {children}
    </ThemeProvider>,
  );
}

describe("ThemeContext", () => {
  beforeEach(() => {
    // Clear localStorage before each test
    localStorage.clear();
    // Reset document.documentElement.dataset.theme
    delete document.documentElement.dataset.theme;
    vi.restoreAllMocks();
  });

  describe("default behavior", () => {
    it("defaults to light theme when no saved preference exists", () => {
      renderThemeProvider();
      expect(screen.getByTestId("theme-value")).toHaveTextContent("light");
    });

    it("sets data-theme attribute to light by default", async () => {
      renderThemeProvider();
      // Wait for effect to run
      await waitFor(() => {
        expect(document.documentElement.dataset.theme).toBe("light");
      });
    });
  });

  describe("loading saved preference", () => {
    it("loads light theme from localStorage when valid", async () => {
      localStorage.setItem("learnloop-theme", "light");
      renderThemeProvider();
      expect(screen.getByTestId("theme-value")).toHaveTextContent("light");
      await waitFor(() => {
        expect(document.documentElement.dataset.theme).toBe("light");
      });
    });

    it("loads dark theme from localStorage when valid", async () => {
      localStorage.setItem("learnloop-theme", "dark");
      renderThemeProvider();
      expect(screen.getByTestId("theme-value")).toHaveTextContent("dark");
      await waitFor(() => {
        expect(document.documentElement.dataset.theme).toBe("dark");
      });
    });

    it("falls back to light theme for invalid saved values", async () => {
      localStorage.setItem("learnloop-theme", "invalid");
      renderThemeProvider();
      expect(screen.getByTestId("theme-value")).toHaveTextContent("light");
      await waitFor(() => {
        expect(document.documentElement.dataset.theme).toBe("light");
      });
    });
  });

  describe("toggling theme", () => {
    it("toggles from light to dark", async () => {
      renderThemeProvider();
      expect(screen.getByTestId("theme-value")).toHaveTextContent("light");

      await act(async () => {
        fireEvent.click(screen.getByTestId("toggle-button"));
      });

      expect(screen.getByTestId("theme-value")).toHaveTextContent("dark");
      await waitFor(() => {
        expect(document.documentElement.dataset.theme).toBe("dark");
        expect(localStorage.getItem("learnloop-theme")).toBe("dark");
      });
    });

    it("toggles from dark to light", async () => {
      localStorage.setItem("learnloop-theme", "dark");
      renderThemeProvider();
      expect(screen.getByTestId("theme-value")).toHaveTextContent("dark");

      await act(async () => {
        fireEvent.click(screen.getByTestId("toggle-button"));
      });

      expect(screen.getByTestId("theme-value")).toHaveTextContent("light");
      await waitFor(() => {
        expect(document.documentElement.dataset.theme).toBe("light");
        expect(localStorage.getItem("learnloop-theme")).toBe("light");
      });
    });
  });

  describe("setTheme", () => {
    it("sets theme to dark", async () => {
      renderThemeProvider();

      await act(async () => {
        fireEvent.click(screen.getByTestId("set-dark-button"));
      });

      expect(screen.getByTestId("theme-value")).toHaveTextContent("dark");
      await waitFor(() => {
        expect(document.documentElement.dataset.theme).toBe("dark");
        expect(localStorage.getItem("learnloop-theme")).toBe("dark");
      });
    });

    it("sets theme to light", async () => {
      localStorage.setItem("learnloop-theme", "dark");
      renderThemeProvider();

      await act(async () => {
        fireEvent.click(screen.getByTestId("set-light-button"));
      });

      expect(screen.getByTestId("theme-value")).toHaveTextContent("light");
      await waitFor(() => {
        expect(document.documentElement.dataset.theme).toBe("light");
        expect(localStorage.getItem("learnloop-theme")).toBe("light");
      });
    });
  });

  describe("useTheme guard", () => {
    it("throws error when used outside ThemeProvider", () => {
      // Suppress the error from being logged in test output
      const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

      function BadConsumer() {
        useTheme();
        return null;
      }

      expect(() => render(<BadConsumer />)).toThrow(
        "useTheme must be used within ThemeProvider",
      );

      consoleErrorSpy.mockRestore();
    });
  });
});