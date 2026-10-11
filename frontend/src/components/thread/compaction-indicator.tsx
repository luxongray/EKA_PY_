"use client";

import { AlertTriangle, Check, LoaderCircle } from "lucide-react";
import type { ReactNode } from "react";
import type { CompactionState } from "@/lib/eka-types";

/**
 * 上下文压缩提示条：压缩发生在每轮回答之前，故渲染在本轮 AI 消息上方。
 * 没有它的话，压缩期间（可能持续数分钟）前端只有一个转圈，用户会以为服务卡死——
 * 与 Codex 给压缩加 indicator 要解决的是同一个问题
 */
export function CompactionIndicator({ state }: { state: CompactionState }) {
  // 压缩进行中：该阶段可能持续一两分钟且期间没有任何文本输出，
  // 必须让用户明确知道"后端在干活"而不是卡死，故做成带底色的提示块而非一行灰字；
  // 同时 thread/index 会隐藏转圈，避免两处 loading 打架
  if (state.phase === "compacting") {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "10px 14px",
          borderRadius: 10,
          background: "#f3f4f6",
          fontSize: 13,
          lineHeight: "20px",
        }}
      >
        <LoaderCircle
          className="animate-spin"
          style={{ width: 16, height: 16, flexShrink: 0, color: "#6b7280" }}
        />
        <div style={{ display: "flex", flexDirection: "column" }}>
          <span style={{ color: "#374151", fontWeight: 500 }}>
            正在压缩对话上下文…
          </span>
          <span style={{ fontSize: 12, color: "#9ca3af" }}>
            历史消息较长，正在整理要点，可能需要一两分钟
          </span>
        </div>
      </div>
    );
  }

  // 压缩成功：保留展示，让用户知道刚才发生了什么；
  // 措辞刻意强调"聊天记录不受影响"——压缩只影响送给模型的记忆，用户看到的历史一条都不会少，
  // 同时不展示归档条数，避免用户误以为自己的消息被删掉或替换
  if (state.phase === "done") {
    return (
      <Row tone="#15803d">
        <Check style={{ width: 14, height: 14, flexShrink: 0 }} />
        <span>对话上下文已压缩，聊天记录不受影响</span>
      </Row>
    );
  }

  // 压缩失败：本轮按原上下文继续，但需让用户知道上下文可能偏长
  return (
    <Row tone="#b45309">
      <AlertTriangle style={{ width: 14, height: 14, flexShrink: 0 }} />
      <span>上下文压缩失败：{state.message}</span>
    </Row>
  );
}

/** 统一提示行样式：小字次要信息，关键布局用内联样式（Tailwind 类在本项目有不生成的先例） */
function Row({ tone, children }: { tone: string; children: ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        fontSize: 13,
        lineHeight: "20px",
        color: tone,
      }}
    >
      {children}
    </div>
  );
}
