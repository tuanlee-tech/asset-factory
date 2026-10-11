import {AbsoluteFill, Audio, Img, Series, useVideoConfig} from "remotion";
import type {CalculateMetadataFunction} from "remotion";
import {Video} from "@remotion/media";

export type FactoryClip = {
  readonly name: string;
  readonly kind: "video" | "image";
  readonly src: string;
  readonly durationSec: number;
};

export type FactoryCutProps = {
  readonly clips: FactoryClip[];
  readonly audioSrc: string | null;
  readonly bg: string;
};

export const FACTORY_FPS = 30;

export const calculateFactoryCutMetadata: CalculateMetadataFunction<FactoryCutProps> =
  async ({props}) => {
    const total = props.clips.reduce(
      (sum, c) => sum + Math.max(0.2, c.durationSec) * FACTORY_FPS,
      0,
    );
    return {durationInFrames: Math.max(FACTORY_FPS, Math.round(total))};
  };

export const FactoryCut = ({clips, audioSrc, bg}: FactoryCutProps) => {
  const {fps} = useVideoConfig();

  if (!clips.length) {
    return (
      <AbsoluteFill
        style={{backgroundColor: "#101828", justifyContent: "center", alignItems: "center"}}
      >
        <h1 style={{color: "#fff", fontFamily: "sans-serif", fontSize: 48}}>
          FactoryCut: them clip trong Studio hoac render qua API
        </h1>
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill style={{backgroundColor: bg}}>
      <Series>
        {clips.map((clip, i) => (
          <Series.Sequence
            key={clip.name + "-" + i}
            name={clip.name}
            durationInFrames={Math.max(
              1,
              Math.round(Math.max(0.2, clip.durationSec) * fps),
            )}
            premountFor={fps}
          >
            {clip.kind === "video" ? (
              <Video src={clip.src} trimBefore={0} premountFor={fps} />
            ) : (
              <Img
                src={clip.src}
                style={{width: "100%", height: "100%", objectFit: "contain"}}
              />
            )}
          </Series.Sequence>
        ))}
      </Series>
      {audioSrc ? <Audio src={audioSrc} /> : null}
    </AbsoluteFill>
  );
};