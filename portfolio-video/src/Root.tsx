import "./index.css";
import { Composition } from "remotion";
import { MyComposition } from "./Composition";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="BdrPipelineDemo"
        component={MyComposition}
        durationInFrames={6570}
        fps={30}
        width={1280}
        height={720}
      />
    </>
  );
};
