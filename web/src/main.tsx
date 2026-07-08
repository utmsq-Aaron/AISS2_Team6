import { QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import { PinGate } from "./components/PinGate";
import "./index.css";
import { queryClient } from "./lib/queryClient";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <PinGate>
          <App />
        </PinGate>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
