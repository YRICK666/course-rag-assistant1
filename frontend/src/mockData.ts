import type { ChatMessage, Material, SourceReference, Subject } from "./types";

export const subjects: Subject[] = [
  { name: "形式语言与自动机" },
  { name: "操作系统" },
  { name: "软件工程" }
];

export const materials: Material[] = [
  {
    id: "os-01",
    fileName: "第1章 操作系统概述.pptx",
    relativePath: "第1章 操作系统概述.pptx",
    fileType: "PPTX",
    sizeLabel: "6.8 MB",
    chapter: "第1章",
    status: "已建库",
    category: "课程基础",
    lastUsed: "今天"
  },
  {
    id: "os-02",
    fileName: "第2章 进程管理.pptx",
    relativePath: "第2章 进程管理.pptx",
    fileType: "PPTX",
    sizeLabel: "8.4 MB",
    chapter: "第2章",
    status: "已建库",
    category: "核心概念",
    lastUsed: "昨天"
  },
  {
    id: "os-03",
    fileName: "第3章 处理器调度.pptx",
    relativePath: "第3章 处理器调度.pptx",
    fileType: "PPTX",
    sizeLabel: "7.1 MB",
    chapter: "第3章",
    status: "未建库",
    category: "核心概念",
    lastUsed: "3 天前"
  },
  {
    id: "os-08",
    fileName: "第8章 文件管理 8-1 基本概念.pptx",
    relativePath: "第8章 文件管理 8-1 基本概念.pptx",
    fileType: "PPTX",
    sizeLabel: "9.6 MB",
    chapter: "第8章",
    status: "已建库",
    category: "存储系统",
    lastUsed: "本周"
  },
  {
    id: "lab-01",
    fileName: "实验1 进程调度实验.docx",
    relativePath: "实验1 进程调度实验.docx",
    fileType: "DOCX",
    sizeLabel: "412 KB",
    chapter: "实验1",
    status: "待转换",
    category: "实验资料",
    lastUsed: "本周"
  }
];

export const sourceReferences: SourceReference[] = [
  {
    id: "src-1",
    fileName: "第1章 操作系统概述.pptx",
    location: "第 6 张幻灯片",
    similarity: 0.91,
    summary: "介绍操作系统作为用户与硬件之间的接口，负责资源管理、程序控制与服务提供。"
  },
  {
    id: "src-2",
    fileName: "第2章 进程管理.pptx",
    location: "第 12 张幻灯片",
    similarity: 0.88,
    summary: "说明进程的定义、状态转换、PCB 结构，以及进程调度与同步的基础关系。"
  },
  {
    id: "src-3",
    fileName: "实验1 进程调度实验.docx",
    location: "第 3 段",
    similarity: 0.82,
    summary: "实验要求学生模拟短作业优先、时间片轮转等调度策略，并比较平均周转时间。"
  }
];

export const initialMessages: ChatMessage[] = [
  {
    role: "user",
    content: "第1章和第2章分别讲了什么？"
  },
  {
    role: "assistant",
    content:
      "第1章主要建立操作系统的整体认知：它是什么、为什么需要它，以及它如何在硬件和应用之间提供统一服务。重点包括资源管理、用户接口、系统调用和操作系统的发展脉络。[1]\n\n第2章进入进程管理。它把程序运行时的实体抽象为进程，并围绕进程状态、进程控制块、调度、同步与通信展开。学习时可以把“进程是什么”和“系统如何管理进程”作为两条主线。[2]\n\n复习建议：先用第1章搭出全局框架，再用第2章理解 CPU、内存和 I/O 资源为什么需要调度与协调。"
  }
];
