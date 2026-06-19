import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { AuthProvider, useAuth } from "@/contexts/AuthContext";
import { useTheme } from "@/contexts/ThemeContext";
import { ProtectedRoute } from "@/components/ProtectedRoute";

import { LoginPage } from "@/pages/LoginPage";
import { RegisterPage } from "@/pages/RegisterPage";
import { ProblemsPage } from "@/pages/ProblemsPage";
import { ProblemDetailPage } from "@/pages/ProblemDetailPage";
import { IngestPage } from "@/pages/IngestPage";
import { ActiveExamPage } from "@/pages/ActiveExamPage";
import { ExamsPage } from "@/pages/ExamsPage";
import { ExamDetailPage } from "@/pages/ExamDetailPage";
import { TagsPage } from "@/pages/TagsPage";
import { PracticePage } from "@/pages/PracticePage";
import { ActivePracticePage } from "@/pages/ActivePracticePage";
import { SettingsPage } from "@/pages/SettingsPage";
import { CoachingPage } from "@/pages/CoachingPage";

function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const location = useLocation();

  const navItems = [
    { label: "Problems", path: "/problems" },
    { label: "Ingest", path: "/ingest" },
    { label: "Exams", path: "/exams" },
    { label: "Tags", path: "/tags" },
    { label: "Practice", path: "/practice" },
    { label: "Settings", path: "/settings" },
  ];

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <>
      <header
        className="glass-header"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "1.5rem",
          padding: "0.875rem 1.5rem",
          position: "sticky",
          top: 0,
          zIndex: 10,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "2rem", flexWrap: "wrap" }}>
          <strong 
            style={{ 
              fontSize: "1.25rem", 
              fontWeight: 800, 
              letterSpacing: "-0.03em",
              display: "flex",
              alignItems: "center",
              gap: "0.25rem"
            }}
          >
            <span className="text-gradient">Learn</span>
            <span style={{ color: "var(--color-text)" }}>Loop</span>
            <span style={{ width: "6px", height: "6px", backgroundColor: "var(--color-primary)", borderRadius: "50%", display: "inline-block" }} />
          </strong>
          <nav aria-label="Primary" style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
            {navItems.map((item) => {
              const active = location.pathname === item.path || location.pathname.startsWith(`${item.path}/`);
              return (
                <button
                  key={item.path}
                  type="button"
                  onClick={() => navigate(item.path)}
                  className="btn"
                  style={{
                    padding: "0.5rem 0.875rem",
                    borderRadius: "var(--radius-md)",
                    border: active ? "1px solid var(--color-primary)" : "1px solid transparent",
                    backgroundColor: active ? "var(--color-primary-bg)" : "transparent",
                    color: active ? "var(--color-primary-text)" : "var(--color-text-muted)",
                    cursor: "pointer",
                    fontWeight: active ? 700 : 500,
                    fontSize: "0.875rem",
                    boxShadow: active ? "var(--shadow-sm)" : "none",
                  }}
                >
                  {item.label}
                </button>
              );
            })}
          </nav>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <button
            type="button"
            onClick={toggleTheme}
            aria-label="Toggle theme"
            className="btn btn-secondary"
            style={{
              padding: "0.5rem 0.875rem",
              fontSize: "0.875rem",
              borderRadius: "var(--radius-md)",
              display: "flex",
              alignItems: "center",
              gap: "0.375rem"
            }}
          >
            {theme === "light" ? "Dark" : "Light"}
          </button>
          
          <div 
            style={{ 
              display: "flex", 
              alignItems: "center", 
              gap: "0.5rem",
              padding: "0.375rem 0.75rem",
              backgroundColor: "var(--color-surface-muted)",
              borderRadius: "var(--radius-full)",
              border: "1px solid var(--color-border)"
            }}
          >
            <div 
              style={{ 
                width: "8px", 
                height: "8px", 
                borderRadius: "50%", 
                backgroundColor: "var(--color-success)" 
              }} 
            />
            <span style={{ color: "var(--color-text)", fontWeight: 600, fontSize: "0.825rem" }}>
              {user?.username}
            </span>
          </div>

          <button
            type="button"
            onClick={() => void handleLogout()}
            className="btn btn-secondary"
            style={{
              padding: "0.5rem 0.875rem",
              fontSize: "0.875rem",
              borderRadius: "var(--radius-md)"
            }}
          >
            Logout
          </button>
        </div>
      </header>
      {children}
    </>
  );
}

function ProtectedPage({ children }: { children: ReactNode }) {
  return (
    <ProtectedRoute>
      <AppShell>{children}</AppShell>
    </ProtectedRoute>
  );
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route
        path="/problems"
        element={
          <ProtectedPage>
            <ProblemsPage />
          </ProtectedPage>
        }
      />
      <Route
        path="/problems/:id"
        element={
          <ProtectedPage>
            <ProblemDetailPage />
          </ProtectedPage>
        }
      />
      <Route
        path="/ingest"
        element={
          <ProtectedPage>
            <IngestPage />
          </ProtectedPage>
        }
      />
      <Route
        path="/exams/active"
        element={
          <ProtectedPage>
            <ActiveExamPage />
          </ProtectedPage>
        }
      />
      <Route
        path="/exams"
        element={
          <ProtectedPage>
            <ExamsPage />
          </ProtectedPage>
        }
      />
      <Route
        path="/exams/:id"
        element={
          <ProtectedPage>
            <ExamDetailPage />
          </ProtectedPage>
        }
      />
      <Route
        path="/tags"
        element={
          <ProtectedPage>
            <TagsPage />
          </ProtectedPage>
        }
      />
      <Route
        path="/practice"
        element={
          <ProtectedPage>
            <PracticePage />
          </ProtectedPage>
        }
      />
      <Route
        path="/practice/active"
        element={
          <ProtectedPage>
            <ActivePracticePage />
          </ProtectedPage>
        }
      />
      <Route
        path="/settings"
        element={
          <ProtectedPage>
            <SettingsPage />
          </ProtectedPage>
        }
      />
      <Route
        path="/coaching/:problemId"
        element={
          <ProtectedPage>
            <CoachingPage />
          </ProtectedPage>
        }
      />
      <Route path="/" element={<Navigate to="/problems" replace />} />
      <Route path="*" element={<Navigate to="/problems" replace />} />
    </Routes>
  );
}
export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}
