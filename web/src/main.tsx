import { QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { PinGate } from "./components/PinGate";
import { devLogin } from "./lib/api";
import "./index.css";
import { queryClient } from "./lib/queryClient";
import { useAuthStore } from "./store/authStore";

const devAutoLoginEmail = String(import.meta.env.VITE_DEV_AUTO_LOGIN_EMAIL ?? "").trim();
if (devAutoLoginEmail && !localStorage.getItem("fitdash.user")) {
  void (async () => {
    try {
      const r = await devLogin();
      useAuthStore.getState().login(r.user, r.is_admin);
    } catch {
      // Login page stays available as the fallback if the dev bootstrap fails.
    }
  })();
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary scope="app">
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <PinGate>
            <App />
          </PinGate>
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
