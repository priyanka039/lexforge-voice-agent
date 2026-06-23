// AudioWorklet processor: forwards raw mono Float32 mic frames to the main
// thread, which downsamples to 16 kHz PCM16 for Gemini STT. Buffers ~20 ms
// to keep message volume low.
class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = [];
    this._target = Math.round(sampleRate * 0.02); // ~20ms of samples
  }

  process(inputs) {
    const input = inputs[0];
    if (input && input[0]) {
      const ch = input[0];
      for (let i = 0; i < ch.length; i++) this._buf.push(ch[i]);
      if (this._buf.length >= this._target) {
        this.port.postMessage({ samples: Float32Array.from(this._buf), rate: sampleRate });
        this._buf = [];
      }
    }
    return true;
  }
}

registerProcessor('pcm-processor', PCMProcessor);
