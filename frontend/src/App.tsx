import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";


function HomePage() {
  return (
    <main>
      <h1>LearnLoop</h1>
      <p>Frontend scaffold is ready.</p>
    </main>
  );
}


export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
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
      <AppRoutes />
    </BrowserRouter>
  );
}
