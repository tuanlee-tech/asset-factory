import {Composition} from "remotion";
import {FactoryCut, calculateFactoryCutMetadata} from "./FactoryCut";

export const RemotionRoot = () => {
  return (
    <>
      <Composition
        id="FactoryCut"
        component={FactoryCut}
        durationInFrames={150}
        fps={30}
        width={1280}
        height={720}
        defaultProps={{clips: [], audioSrc: null, bg: "#101828"}}
        calculateMetadata={calculateFactoryCutMetadata}
      />
    </>
  );
};