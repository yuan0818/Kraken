import { useRef } from "react";
import { Alert, Button, Image, Tag } from "antd";
import type { ExecutionReport } from "@kraken/api-client";
import { engineText } from "./ui";

type Check = NonNullable<ExecutionReport["quality_checks"]>[number];
const names: Record<string, string> = {
  screen_corruption: "花屏", black_screen: "黑屏", green_screen: "绿屏",
  blocking_artifact: "压缩块", mosaic: "马赛克", color_space_error: "色彩异常",
  noise: "噪声", harsh: "刺耳声", clip_intermittent: "声音间歇中断",
  distortion: "失真", hum: "嗡声", silence_loss: "异常静音", loudness_anomaly: "响度异常",
};
export function QualityEvidence({ attemptId, checks }: { attemptId: string; checks: Check[] }) {
  if (!checks.length) return null;
  return <section className="quality-evidence" aria-label="效果检测证据">
    <h3>画面与声音效果</h3>
    <p className="body-note">模型检测独立于操作是否完成。辅助诊断、采集不足或检测失败均不能作为通过依据。</p>
    {checks.map(check => <QualityCard key={check.id} attemptId={attemptId} check={check} />)}
  </section>;
}
function QualityCard({ attemptId, check }: { attemptId: string; check: Check }) {
  const player = useRef<HTMLVideoElement>(null);
  const url = `/api/attempts/${attemptId}/quality-media/${check.evidence_digest}`;
  const events = check.events || [];
  const video = check.evidence_type === "video/mp4";
  const labels = { passed: "通过", failed: "失败", inconclusive: "无法判定" };
  return <article className="quality-check">
    <div className="quality-check-heading"><strong>{check.tool === "check_visual_quality" ? "画面检测" : "声音检测"}</strong>
      <Tag color={check.verdict === "passed" ? "green" : check.verdict === "failed" ? "red" : "gold"}>{labels[check.verdict]}</Tag>
      {check.policy === "advisory" && <Tag>辅助诊断</Tag>}
    </div>
    <p>{engineText(check.criterion)}</p>
    <p className="body-note">{video ? `执行会话 ${check.start_seconds.toFixed(1)}–${check.end_seconds.toFixed(1)} 秒` : "当前画面"} · {check.checks.map(c => names[c] || c).join("、")}
      {check.step_number != null && ` · 关联步骤 ${check.step_number}`}</p>
    {check.status !== "completed" && <Alert type="warning" showIcon message="本次检测未得到完整结论" description={engineText(check.reason)} />}
    <div className="quality-check-content">
      <div className="quality-media">
        {check.evidence_available ? video
          ? <video ref={player} controls preload="metadata" src={url} aria-label="效果检测录屏证据" />
          : <Image src={url} alt="效果检测原始画面" />
          : <p className="body-note">未获得媒体证据，无法回放。</p>}
      </div>
      <div className="quality-findings">
        <strong>{events.length ? `发现 ${events.length} 个异常记录` : check.status === "completed" ? "已分析样本未检出所选异常" : "暂无有效检测结果"}</strong>
        {check.sample_interval_seconds && <p className="body-note">每 {check.sample_interval_seconds} 秒采样；更短暂的异常可能漏检。画面异常时间为采样点，不代表连续持续时间。</p>}
        {events.map((event, index) => <div className="quality-event" key={index}>
          <span>{names[event.kind] || event.kind}</span>
          <Button type="link" disabled={!video || !check.evidence_available} onClick={() => { if (player.current) player.current.currentTime = event.start_seconds; }}>
            {event.start_seconds.toFixed(2)}–{event.end_seconds.toFixed(2)} 秒
          </Button>
          {event.score != null && <small>模型分数 {event.score.toFixed(3)}</small>}
        </div>)}
        {video && <p className="body-note">异常时间相对此片段起点，点击时间定位回放。</p>}
      </div>
    </div>
    <details><summary>检测依据</summary><p>{check.detector} · 权重 {check.model_sha256 || "未加载"}</p><p>{engineText(check.reason)}</p><p>已分析 {check.analyzed_seconds} 秒{check.threshold != null && ` · 阈值 ${check.threshold}`}</p></details>
  </article>;
}
