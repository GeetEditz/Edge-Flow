import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { LiveProvider } from "./api/live";
import { Layout } from "./components/Layout";
import { ToastProvider } from "./components/ui";
import { Overview } from "./pages/Overview";
import { Servers } from "./pages/Servers";
import { RoutingLab } from "./pages/RoutingLab";
import { CacheLab } from "./pages/CacheLab";
import { Traffic } from "./pages/Traffic";
import { Experiments } from "./pages/Experiments";
import { TopologyPage } from "./pages/Topology";
import { NotFound } from "./pages/NotFound";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <ToastProvider>
        <LiveProvider>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<Overview />} />
              <Route path="servers" element={<Servers />} />
              <Route path="routing" element={<RoutingLab />} />
              <Route path="cache" element={<CacheLab />} />
              <Route path="traffic" element={<Traffic />} />
              <Route path="experiments" element={<Experiments />} />
              <Route path="experiments/:id" element={<Experiments />} />
              <Route path="topology" element={<TopologyPage />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </LiveProvider>
      </ToastProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
