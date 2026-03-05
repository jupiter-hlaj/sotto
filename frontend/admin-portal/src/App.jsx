import { BrowserRouter, Routes, Route, Navigate, NavLink, useNavigate } from "react-router-dom";
import { isAuthenticated, clearTokens } from "./services/api";
import Login from "./pages/Login";
import Signup from "./pages/Signup";
import Dashboard from "./pages/Dashboard";
import Agents from "./pages/Agents";
import NumberMappings from "./pages/NumberMappings";
import CallHistory from "./pages/CallHistory";
import CallDetail from "./pages/CallDetail";
import Settings from "./pages/Settings";

function ProtectedRoute({ children }) {
  if (!isAuthenticated()) return <Navigate to="/login" replace />;
  return children;
}

function Layout({ children }) {
  const navigate = useNavigate();

  function handleLogout() {
    clearTokens();
    navigate("/login");
  }

  const link = "px-3 py-2 rounded text-sm font-medium";
  const active = "bg-indigo-700 text-white";
  const inactive = "text-indigo-100 hover:bg-indigo-600";

  return (
    <div className="min-h-screen">
      <nav className="bg-indigo-800">
        <div className="mx-auto max-w-7xl px-4 flex items-center h-14 gap-1">
          <span className="text-white font-bold text-lg mr-6">Sotto</span>
          <NavLink to="/dashboard" className={({ isActive }) => `${link} ${isActive ? active : inactive}`}>Dashboard</NavLink>
          <NavLink to="/agents" className={({ isActive }) => `${link} ${isActive ? active : inactive}`}>Agents</NavLink>
          <NavLink to="/numbers" className={({ isActive }) => `${link} ${isActive ? active : inactive}`}>Numbers</NavLink>
          <NavLink to="/calls" className={({ isActive }) => `${link} ${isActive ? active : inactive}`}>Calls</NavLink>
          <NavLink to="/settings" className={({ isActive }) => `${link} ${isActive ? active : inactive}`}>Settings</NavLink>
          <div className="flex-1" />
          <button onClick={handleLogout} className="text-indigo-200 hover:text-white text-sm">Logout</button>
        </div>
      </nav>
      <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/signup" element={<Signup />} />
        <Route path="/dashboard" element={<ProtectedRoute><Layout><Dashboard /></Layout></ProtectedRoute>} />
        <Route path="/agents" element={<ProtectedRoute><Layout><Agents /></Layout></ProtectedRoute>} />
        <Route path="/numbers" element={<ProtectedRoute><Layout><NumberMappings /></Layout></ProtectedRoute>} />
        <Route path="/calls" element={<ProtectedRoute><Layout><CallHistory /></Layout></ProtectedRoute>} />
        <Route path="/calls/:callId" element={<ProtectedRoute><Layout><CallDetail /></Layout></ProtectedRoute>} />
        <Route path="/settings" element={<ProtectedRoute><Layout><Settings /></Layout></ProtectedRoute>} />
        <Route path="*" element={<Navigate to={isAuthenticated() ? "/dashboard" : "/login"} replace />} />
      </Routes>
    </BrowserRouter>
  );
}
