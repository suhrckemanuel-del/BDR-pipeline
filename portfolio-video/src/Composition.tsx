import { Audio } from "@remotion/media";
import {
  AbsoluteFill,
  Img,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

const fps = 30;
const seconds = (value: number) => Math.round(value * fps);

type Scene = {
  start: number;
  end: number;
  image: string;
  title: string;
  label: string;
  zoom?: number;
  x?: number;
  y?: number;
  tone?: "default" | "proof" | "caveat";
};

type Caption = {
  start: number;
  end: number;
  text: string;
};

const scenes: Scene[] = [
  {
    start: 0,
    end: 8.83,
    image: "08-architecture-diagram.png",
    title: "AI BDR Pipeline",
    label: "Portfolio proof artifact",
    zoom: 1.0,
  },
  {
    start: 8.83,
    end: 22.67,
    image: "08-architecture-diagram.png",
    title: "Company input -> researched outreach",
    label: "Signals, angle, sequence, critique",
    zoom: 1.02,
    x: -24,
  },
  {
    start: 22.67,
    end: 35.55,
    image: "07-final-output-or-report.png",
    title: "Public version uses anonymized demos",
    label: "No revenue or campaign claims",
    tone: "caveat",
    zoom: 1.16,
    x: 18,
    y: -76,
  },
  {
    start: 35.55,
    end: 50.31,
    image: "05-outreach-sequence.png",
    title: "Multi-agent BDR workflow",
    label: "Live signals + tenant positioning + critic pass",
    tone: "proof",
    zoom: 1.18,
    x: 24,
    y: 4,
  },
  {
    start: 50.31,
    end: 77.03,
    image: "01-streamlit-input.png",
    title: "Problem: outbound prep is slow",
    label: "Research, angle, copy, follow-ups, review",
    zoom: 1.22,
    x: 210,
    y: 0,
  },
  {
    start: 77.03,
    end: 104.43,
    image: "02-research-signals.png",
    title: "Stage 1: enrichment",
    label: "Exa signals, Hunter contacts, Claude summary",
    zoom: 1.22,
    x: -30,
    y: -88,
  },
  {
    start: 104.43,
    end: 117.55,
    image: "04-positioning-angles.png",
    title: "Stage 2: strategist",
    label: "Chooses one tenant-defined outreach angle",
    zoom: 1.19,
    x: -22,
    y: -34,
  },
  {
    start: 117.55,
    end: 138.15,
    image: "04-positioning-angles.png",
    title: "Stage 3: humanizer",
    label: "Specific observations + tenant copy banks",
    zoom: 1.22,
    x: -22,
    y: 12,
  },
  {
    start: 138.15,
    end: 147.23,
    image: "05-outreach-sequence.png",
    title: "Output: multi-touch sequence",
    label: "LinkedIn, email, follow-up, social proof, breakup",
    tone: "proof",
    zoom: 1.24,
    x: -8,
    y: 26,
  },
  {
    start: 147.23,
    end: 166.31,
    image: "06-critic-quality-pass.png",
    title: "Quality gate: critic pass",
    label: "Pain, proof, CTA clarity, human voice",
    zoom: 1.18,
    x: -12,
    y: -42,
  },
  {
    start: 166.31,
    end: 182.03,
    image: "03-contact-discovery.png",
    title: "Demo caveat: fictional companies",
    label: "Thin contact discovery is expected in public demo data",
    tone: "caveat",
    zoom: 1.2,
    x: 32,
    y: -8,
  },
  {
    start: 182.03,
    end: 205.15,
    image: "08-architecture-diagram.png",
    title: "Next improvements",
    label: "Reply tracking, CRM export, analytics, ICP scoring",
    zoom: 1.03,
    x: 26,
  },
  {
    start: 205.15,
    end: 219,
    image: "07-final-output-or-report.png",
    title: "Practical AI workflows for business problems",
    label: "Clear stages, review loop, reusable workflow design",
    tone: "proof",
    zoom: 1.13,
    x: 12,
    y: -56,
  },
];

const captions: Caption[] = [
  { start: 0.43, end: 8.83, text: "BDR pipeline proof artifact for AI-assisted GTM workflows." },
  { start: 8.83, end: 22.67, text: "Target company -> signals -> angle -> sequence -> critic pass." },
  { start: 22.67, end: 35.55, text: "Anonymized public demo. No revenue or campaign claims." },
  { start: 35.55, end: 50.31, text: "Multi-agent workflow for researched outreach." },
  { start: 50.31, end: 66.67, text: "Outbound prep is slow: research, persona, angle, emails, follow-ups, review." },
  { start: 66.67, end: 77.03, text: "A repeatable workflow instead of a one-off prompt." },
  { start: 77.03, end: 84.55, text: "Streamlit UI. LangGraph orchestration." },
  { start: 84.55, end: 104.43, text: "Exa signals, Hunter contacts when available, Claude account summary." },
  { start: 104.43, end: 117.55, text: "Strategist chooses one tenant-defined outreach angle." },
  { start: 117.55, end: 138.15, text: "Humanizer combines model observations with tenant copy banks." },
  { start: 138.15, end: 147.23, text: "LinkedIn, email, follow-up, social proof, DM, breakup." },
  { start: 147.23, end: 166.31, text: "Critic scores pain, proof, CTA clarity, and human voice." },
  { start: 166.31, end: 182.03, text: "Fictional companies mean limited contact discovery in the public demo." },
  { start: 182.03, end: 205.15, text: "Next: reply tracking, CRM export, analytics, ICP scoring, learning loop." },
  { start: 205.15, end: 216.55, text: "Practical AI workflows for real business problems." },
];

const getActiveCaption = (time: number) =>
  captions.find((caption) => time >= caption.start && time < caption.end);

const SceneView: React.FC<{ scene: Scene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const duration = seconds(scene.end - scene.start);
  const opacity = Math.min(
    interpolate(frame, [0, 12], [0, 1], { extrapolateRight: "clamp" }),
    interpolate(frame, [duration - 12, duration], [1, 0], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    }),
  );
  const progress = duration <= 0 ? 0 : frame / duration;
  const scale = interpolate(progress, [0, 1], [scene.zoom ?? 1.04, (scene.zoom ?? 1.04) + 0.022]);
  const shiftX = interpolate(progress, [0, 1], [scene.x ?? 0, (scene.x ?? 0) - 8]);
  const shiftY = interpolate(progress, [0, 1], [scene.y ?? 0, (scene.y ?? 0) - 6]);

  return (
    <AbsoluteFill style={{ opacity }}>
      <div className="screen-stage">
        <Img
          src={staticFile(scene.image)}
          className="demo-screen"
          style={{
            transform: `translate(${shiftX}px, ${shiftY}px) scale(${scale})`,
          }}
        />
        <div className="soft-vignette" />
      </div>
      <div className={`mini-callout ${scene.tone ?? "default"}`}>
        <div className="mini-label">{scene.label}</div>
        <div className="mini-title">{scene.title}</div>
      </div>
    </AbsoluteFill>
  );
};

const Subtitle: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps: videoFps } = useVideoConfig();
  const time = frame / videoFps;
  const caption = getActiveCaption(time);

  if (!caption) {
    return null;
  }

  return (
    <div className="subtitle-wrap">
      <div className="subtitle">{caption.text}</div>
    </div>
  );
};

export const MyComposition = () => {
  return (
    <AbsoluteFill className="video">
      <Audio src={staticFile("bdr-demo-voiceover.m4a")} />
      {scenes.map((scene) => (
        <Sequence
          key={`${scene.start}-${scene.title}`}
          from={seconds(scene.start)}
          durationInFrames={seconds(scene.end - scene.start)}
          premountFor={seconds(0.5)}
        >
          <SceneView scene={scene} />
        </Sequence>
      ))}
      <Subtitle />
    </AbsoluteFill>
  );
};
