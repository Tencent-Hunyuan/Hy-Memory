import { Component, type ReactNode } from "react";
import { useApplicationsLanguage } from "./applications-language";

type Props = { label: string; children: ReactNode; fallback?: ReactNode };
type State = { crashed: boolean };

const RESET_MS = 15_000;
function CardFallback({ label }: { label: string }) {
  const { a } = useApplicationsLanguage();
  return <p>{a("cardUnavailable", { label })}</p>;
}

/**
 * 2026-10-12: 首页任何一张卡片渲染出错时，只塌这一张卡，不塌整页。
 * 背景： tester 反馈“一报错连布局都乱了”。单卡报错时渲染一张安静的
 * 占位卡，其余区域照常；15 秒后自动重挂载，若数据已恢复则卡片回来。
 * 用法：每张卡片包一层，并传入卡片名用于占位文案。
 */
export class HomeCardBoundary extends Component<Props, State> {
  state: State = { crashed: false };
  resetTimer: ReturnType<typeof setTimeout> | null = null;

  static getDerivedStateFromError(): State {
    return { crashed: true };
  }

  componentDidCatch(error: unknown) {
    console.error(`[home] ${this.props.label}渲染失败：`, error);
    if (this.resetTimer !== null) clearTimeout(this.resetTimer);
    this.resetTimer = setTimeout(() => {
      this.resetTimer = null;
      this.setState({ crashed: false });
    }, RESET_MS);
  }

  componentWillUnmount() {
    if (this.resetTimer !== null) clearTimeout(this.resetTimer);
  }

  render() {
    if (this.state.crashed) {
      return (
        <section className="r6-home-auto-failure" role="status">
          {this.props.fallback ?? <CardFallback label={this.props.label} />}
        </section>
      );
    }
    return this.props.children;
  }
}
