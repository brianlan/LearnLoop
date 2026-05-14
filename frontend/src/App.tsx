import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider } from "@/contexts/AuthContext";
import { ProtectedRoute } from "@/components/ProtectedRoute";

import { LoginPage } from "@/pages/LoginPage";
import { RegisterPage } from "@/pages/RegisterPage";
import { ProblemsPage } from "@/pages/ProblemsPage";
import { ProblemDetailPage } from "@/pages/ProblemDetailPage";
import { IngestPage } from "@/pages/IngestPage";
import { ActiveExamPage } from "@/pages/ActiveExamPage";
import { ExamsPage } from "@/pages/ExamsPage";
import { ExamDetailPage } from "@/pages/ExamDetailPage";

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route
        path="/problems"
        element={
          <ProtectedRoute>
            <ProblemsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/problems/:id"
        element={
          <ProtectedRoute>
            <ProblemDetailPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/ingest"
        element={
          <ProtectedRoute>
            <IngestPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/exams/active"
        element={
          <ProtectedRoute>
            <ActiveExamPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/exams"
        element={
          <ProtectedRoute>
            <ExamsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/exams/:id"
        element={
          <ProtectedRoute>
            <ExamDetailPage />
          </ProtectedRoute>
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
    <BrowserRouter
      future={{
        v7_startTransition: true,
        v7_relativeSplatPath: true,
      }}
    >
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}
