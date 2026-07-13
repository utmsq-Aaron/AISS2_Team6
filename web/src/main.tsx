import { QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { PinGate } from "./components/PinGate";
import "./index.css";
import { queryClient } from "./lib/queryClient";

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
