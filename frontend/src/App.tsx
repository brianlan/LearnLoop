import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { AuthProvider, useAuth } from "@/contexts/AuthContext";
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

function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const navItems = [
    { label: "Problems", path: "/problems" },
    { label: "Ingest", path: "/ingest" },
    { label: "Exams", path: "/exams" },
    { label: "Tags", path: "/tags" },
  ];

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <>
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "1rem",
          padding: "0.75rem 1rem",
          borderBottom: "1px solid #e5e7eb",
          backgroundColor: "#ffffff",
          position: "sticky",
          top: 0,
          zIndex: 10,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "1rem", flexWrap: "wrap" }}>
          <strong>LearnLoop</strong>
          <nav aria-label="Primary" style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
            {navItems.map((item) => {
              const active = location.pathname === item.path || location.pathname.startsWith(`${item.path}/`);
              return (
                <button
                  key={item.path}
                  type="button"
                  onClick={() => navigate(item.path)}
                  style={{
                    padding: "0.5rem 0.75rem",
                    borderRadius: "0.375rem",
                    border: active ? "1px solid #2563eb" : "1px solid #d1d5db",
                    backgroundColor: active ? "#dbeafe" : "#ffffff",
                    color: active ? "#1d4ed8" : "#111827",
                    cursor: "pointer",
                    fontWeight: active ? 600 : 500,
                  }}
                >
                  {item.label}
                </button>
              );
            })}
          </nav>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <span style={{ color: "#4b5563", fontSize: "0.875rem" }}>{user?.username}</span>
          <button
            type="button"
            onClick={() => void handleLogout()}
            style={{
              padding: "0.5rem 0.75rem",
              borderRadius: "0.375rem",
              border: "1px solid #d1d5db",
              backgroundColor: "#ffffff",
              cursor: "pointer",
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
      <Route path="/" element={<Navigate to="/problems" replace />} />
      <Route path="*" element={<Navigate to="/problems" replace />} />
    </Routes>
  );
}

function HomePage() {
  return (
    <main>
      <h1>LearnLoop</h1>
      <p>Frontend scaffold is ready.</p>
    </main>
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
