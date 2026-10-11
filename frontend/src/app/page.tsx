"use client";

import { Thread } from "@/components/thread";
import { EkaProvider } from "@/providers/Eka";
import { ThreadProvider } from "@/providers/Thread";
import { ArtifactProvider } from "@/components/thread/artifact";
import { Toaster } from "@/components/ui/sonner";
import React from "react";

export default function DemoPage(): React.ReactNode {
  return (
    <React.Suspense fallback={<div>加载中...</div>}>
      <Toaster />
      <ThreadProvider>
        <EkaProvider>
          <ArtifactProvider>
            <Thread />
          </ArtifactProvider>
        </EkaProvider>
      </ThreadProvider>
    </React.Suspense>
  );
}
