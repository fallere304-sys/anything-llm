package com.z4motioncam;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.HashMap;
import java.util.Map;

import org.tensorflow.lite.Interpreter;

/**
 * {@link BodyRuntime} on TensorFlow Lite (org.tensorflow:tensorflow-lite 2.8.0, Apache-2.0) for the
 * COCO SSD MobileNet v1 quantized model: input 1x300x300x3 uint8, outputs (postprocessed on the
 * model) boxes [1,10,4], classes [1,10], scores [1,10], count [1]. Compiled only in the PERSON_MODEL=1
 * build (app/src/model); {@link AndroidBodyModel} reaches it by name.
 */
final class TfliteRuntime implements BodyRuntime {
    private final Interpreter interpreter;
    private final ByteBuffer input = ByteBuffer.allocateDirect(BodyInput.BYTES).order(ByteOrder.nativeOrder());
    private final float[][][] boxes = new float[1][BodyOutput.MAX][4];
    private final float[][] classes = new float[1][BodyOutput.MAX];
    private final float[][] scores = new float[1][BodyOutput.MAX];
    private final float[] count = new float[1];
    private final Map<Integer, Object> outputs = new HashMap<>();
    private final Object[] inputs = {input};

    TfliteRuntime(ByteBuffer model, int threads) {
        Interpreter.Options o = new Interpreter.Options();
        o.setNumThreads(threads);
        interpreter = new Interpreter(model, o);
        int[] in = interpreter.getInputTensor(0).shape();
        int[] box = interpreter.getOutputTensor(0).shape();
        if (in.length != 4 || in[1] != BodyInput.SIZE || in[2] != BodyInput.SIZE || in[3] != 3
                || box.length != 3 || box[1] != BodyOutput.MAX || box[2] != 4 || interpreter.getOutputTensorCount() != 4) {
            interpreter.close();
            throw new IllegalArgumentException("unexpected model shape");
        }
        outputs.put(0, boxes);
        outputs.put(1, classes);
        outputs.put(2, scores);
        outputs.put(3, count);
    }

    @Override
    public boolean run(byte[] rgb300, BodyOutput out) {
        input.clear();
        input.put(rgb300, 0, BodyInput.BYTES);
        input.rewind();
        interpreter.runForMultipleInputsOutputs(inputs, outputs);
        int n = Math.max(0, Math.min(BodyOutput.MAX, (int) count[0]));
        for (int i = 0; i < n; i++) {
            System.arraycopy(boxes[0][i], 0, out.boxes, i * 4, 4);
            out.classes[i] = classes[0][i];
            out.scores[i] = scores[0][i];
        }
        out.count = n;
        return true;
    }

    @Override
    public void close() {
        interpreter.close();
    }
}
