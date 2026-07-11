---
layout: post
title: "How LLMs Work, Part 4: Using the Trained Model"
date: 2026-07-11
categories: [ai, ml-foundations, llm-training]
tags: [llm, inference, kv-cache, decoding, top-k, top-p, temperature]
---

![Views](https://hitscounter.dev/api/hit?url=https%3A%2F%2Fshbhmrzd.github.io%2Fai%2Fml-foundations%2Fllm-training%2F2026%2F07%2F11%2Fusing-the-trained-model.html&label=Views&icon=eye&color=%23007ec6&style=flat-square)

# How LLMs Work, Part 4: Using the Trained Model

A while back I wrote a [post on TurboQuant](https://shbhmrzd.github.io/ai/systems/ml-infrastructure/quantization/2026/04/04/turboquant-vector-quantization-for-llm-inference.html), about compressing the KV cache to make inference cheaper. At that point I was reasoning about the size of the KV cache without knowing how it gets filled in the first place, or where the Keys and Values come from. I knew it stored Key and Value vectors, but I could not have explained what actually happens between hitting enter and seeing the first word appear, or why the response streams out one word at a time instead of all at once.

I have been writing up how LLMs work for software engineers who don't have an ML background. This is the last part of the series. Parts [1](https://shbhmrzd.github.io/ai/ml-foundations/llm-training/2026/05/27/how-llms-process-text.html), [2](https://shbhmrzd.github.io/ai/ml-foundations/llm-training/2026/05/29/how-llms-learn.html), and [3](https://shbhmrzd.github.io/ai/ml-foundations/llm-training/2026/06/03/from-toy-model-to-gpt.html) covered how text is processed, how models learn, and how training scales. All of that produces a model sitting in memory as billions of trained parameters. This post covers what happens when you type a prompt and hit enter. How the model generates one token at a time, why that is slow, what the KV cache does about it and how decoding strategies shape the response.

---

## Inference: Autoregressive Generation

During training, the model saw entire sequences and predicted the next token at every position in parallel. But during inference, there is no response to look at. The model has to build it one token at a time.

In [Part 1](https://shbhmrzd.github.io/ai/ml-foundations/llm-training/2026/05/27/how-llms-process-text.html) I covered the forward pass. Token IDs flow through embeddings and transformer layers, and the final layer outputs a score (logit) for every token in the vocabulary. During inference the same forward pass runs, but the model can only predict one new token at a time. It does not know what comes after that because the rest of the response has not been generated yet.

Given a prompt, the model generates a response in the following steps.

1. User types a prompt. The tokenizer converts it to token IDs.
2. Run one forward pass with all the prompt tokens as input. The model produces logits for the token that would come after the entire prompt.
3. Sample one token from those logits.
4. Append it to the sequence.
5. Run another forward pass with the original prompt plus the one new token.
6. Sample the next token.
7. Repeat until you reach the desired length or hit a stop token.

This is **autoregressive generation**. Each step feeds into the next, so the model has to go one token at a time.

![Autoregressive generation: one token at a time](/assets/img/llm_part4_inference/autoregressive_generation.png)

Let me walk through an example. The user sends "What is gravity?", which the tokenizer converts to `[What, is, gravity, ?]` (4 tokens).

**Step 1**: Input is `[What, is, gravity, ?]` (4 tokens from the prompt).

Forward pass produces logits for 128,256 possible next tokens. After SFT and alignment ([Part 3](https://shbhmrzd.github.io/ai/ml-foundations/llm-training/2026/06/03/from-toy-model-to-gpt.html)), the model responds to questions rather than just completing text. "Gravity" gets the highest logit.

Sample "Gravity" (pick it from the probability distribution). Append to sequence.

**Step 2**: Input is now `[What, is, gravity, ?, Gravity]` (5 tokens).

Forward pass produces logits. The token for "is" gets the highest logit.

Sample "is". Append to sequence.

**Step 3**: Input is now `[What, is, gravity, ?, Gravity, is]` (6 tokens).

Forward pass produces logits. The token for "the" gets the highest logit.

Sample "the". Append to sequence.

**Step 4**: Input is now `[What, is, gravity, ?, Gravity, is, the]` (7 tokens).

Forward pass produces logits. The token for "force" gets the highest logit.

Sample "force". Append to sequence.

The model keeps going like this, one token per step, building "Gravity is the force..." until it hits a stop token or a length limit. The mechanics are the same as a base model completing text. The difference is that SFT and alignment (Part 3) shaped the parameters so the model produces answers instead of continuations.

At each step, the model processes the *entire* sequence so far. Step 1 reads 4 tokens. Step 2 reads 5. Step 3 reads 6. At step 100, the model is re-reading tokens 1 to 99, which were already processed in previous steps. Most of that work is redundant.

Unlike training, where the entire sequence is fed at once and all tokens are processed in parallel (causal mask prevents looking ahead), inference is **inherently sequential**. You cannot generate step 2 until step 1 is done, because step 1 produces the token that step 2 needs as input. A 1000-token response means 1000 forward passes. Training processes all 1000 tokens in one.

### Prefill and Decode

In practice, inference splits into two phases.

**Prefill phase**, which processes the entire prompt at once, in parallel. A 50-token prompt runs in one forward pass. The GPU processes all 50 tokens simultaneously through every layer. Each token only attends to the tokens before it (the causal mask from Part 1 prevents looking ahead), but the GPU computes all these attention patterns in one batched operation. This is what GPUs are good at, large matrix multiplications with high parallelism. The forward pass works the same way as during training. The model produces a next-token prediction at every position. But unlike training, those predictions are not used for loss computation or updating weights. The model does not learn from the prompt. The only prediction that matters is at the last position, which becomes the first generated token. The real purpose of prefill is the attention computation. As each token attends to all prior tokens through every layer, the model builds up Key and Value vectors that capture the context of the entire prompt. Those vectors go straight into the KV cache for the decode phase to reuse. Short prompts are fast. Very long prompts (tens of thousands of tokens) can make prefill itself noticeable because the attention computation scales quadratically with sequence length.

**Decode phase**, which generates new tokens one at a time, sequentially. Each forward pass processes just one new token, which means the GPU is doing very little computation per step, a tiny matrix multiplication compared to the massive batch it handled during prefill. The bottleneck shifts from compute to memory bandwidth: the GPU spends most of its time loading the model weights from memory rather than doing math. This is why GPU memory bandwidth (how fast data can be read from the GPU's memory) matters more for decode performance than raw compute speed.

For a typical chat interaction, say a 100-token prompt and a 500-token response, prefill (100 tokens in 1 forward pass) is fast. Decode (500 sequential forward passes, each producing 1 token) is what you feel as latency. This is also why responses stream in one word at a time. Each token genuinely is generated one at a time, and the system sends it to you as soon as it is ready rather than waiting for the full response.

Going back to the "What is gravity?" example. The prompt `[What, is, gravity, ?]` is 4 tokens. During prefill, all 4 tokens are processed in a single forward pass, and the model produces the first generated token ("Gravity"). During decode, the model generates the rest of the response one token at a time: "is", "the", "force", and so on, each requiring its own forward pass.

---

## The KV Cache: Saving Work Across Steps

Remember that during decode, each step processes the full sequence so far. At step 100, that means 100 tokens. But tokens 1 through 99 have not changed since step 99. The attention mechanism recomputes the exact same Key and Value vectors for all of them. That is wasted work.

The **KV cache** fixes this by storing every Key and Value vector after it is computed. At step 100, you only compute K and V for the new token and reuse the cached vectors for tokens 1 through 99.

During prefill, the model runs the forward pass over the full prompt and stores the resulting Key and Value vectors. A 50-token prompt fills the cache with K and V vectors for all 50 positions.

During decode, the model generates token 51 by running a forward pass with just that one token. It computes K and V for token 51, concatenates them with the cached vectors from tokens 1 through 50, and runs attention. For token 52, same thing: compute, concatenate, attend. The cache keeps growing, but old tokens' vectors are never recomputed.

### How Much Memory Does the KV Cache Use?

For Llama 3.1-8B:
- 32 layers. Each transformer layer has its own attention mechanism, so each layer produces its own Key and Value vectors. They cannot be shared across layers.
- 8 KV heads (not 32, for reasons I explain below). In [Part 1](https://shbhmrzd.github.io/ai/ml-foundations/llm-training/2026/05/27/how-llms-process-text.html) I covered how attention uses multiple heads, each learning to focus on different patterns in the text. Each head produces its own Key and Value vectors. Every layer contains all 8 heads, so for each token, the model stores 32 × 8 = 256 separate Key/Value vector pairs.
- head_dim = 128. Each head's Key and Value vectors are 128 numbers long. This is the size of each individual head's working space.
- FP16 precision (2 bytes per value)

Llama 3.1 supports a context length of 128,000 tokens. For this example I will use 4,096 tokens to keep the numbers readable.

KV cache memory:

```
KV cache memory = 2 × num_layers × num_kv_heads × head_dim × sequence_length × bytes_per_value
```

The `2` accounts for both Key and Value. The remaining terms are the model parameters listed above: 32 layers, 8 KV heads, 128 dimensions per head, 2 bytes per value. `sequence_length` is how many tokens are in the cache.

Start with a single token at a single layer and a single head. One Key vector is 128 numbers, each stored in 2 bytes (FP16). That is `128 × 2 = 256 bytes`. The Value vector is the same size, so one token at one head at one layer costs `256 × 2 = 512 bytes` for both K and V.

Each layer has 8 heads, so one token at one layer costs `512 × 8 = 4,096 bytes`. There are 32 layers, so one token across the full model costs `4,096 × 32 = 131,072 bytes ≈ 128 KB`.

That does not sound like much, but it adds up with sequence length. At 4,096 tokens:

```
128 KB × 4,096 = 512 MB per sequence
```

At 128,000 tokens (Llama 3.1's full context length) it is roughly 16 GB for a single sequence.

The numbers above are for a single sequence. When serving multiple users, multiply by the number of concurrent sequences. For 32 users at 4,096 tokens each:

```
512 MB × 32 = 16 GB just for the KV cache
```

The model weights themselves (Llama 3.1-8B in FP16) take about 16 GB. The KV cache for a batch of 32 users costs as much memory as the model itself.

This is where **Grouped Query Attention** (GQA) comes in, which I covered in [Part 1](https://shbhmrzd.github.io/ai/ml-foundations/llm-training/2026/05/27/how-llms-process-text.html). The 8 KV heads I listed above are already the GQA number. Without GQA, the model would use 32 KV heads (one per query head), and the cache would be 4 times larger:

```
2 × 32 × 8  × 128 × 4,096 × 2 =  512 MB per sequence (with GQA, 8 KV heads)
2 × 32 × 32 × 128 × 4,096 × 2 = 2,048 MB per sequence (without GQA, 32 KV heads)
```

GQA groups multiple query heads to share the same Key and Value heads. Some fine-grained attention patterns can be lost, but in practice the memory savings far outweigh the quality difference.

### How Much Work Does the KV Cache Save?

Without the KV cache, generating 1,000 tokens means computing Key and Value vectors from scratch at every step. Step 1 computes them for 1 token. Step 2 for 2. Step 3 for 3. Total:

```
1 + 2 + 3 + ... + 1000 = 1000 * 1001 / 2 ≈ 500,000 K/V computations
```

With the KV cache, each token's K/V vectors are computed exactly once and stored. That is 1,000 computations instead of 500,000.

The cache does not eliminate all redundant work. Each new token's query still attends over every cached key. At step 1, it attends over 1 key. At step 2, over 2 keys. At step 100, over 100 keys. The total number of attention dot products across a full 1,000-token generation is `1 + 2 + 3 + ... + 1000 ≈ 500,000`. That O(n²) cost stays the same with or without the cache. The cache helps because computing K/V vectors is expensive. It involves matrix multiplications at every layer. Without the cache, the model does that for every previous token, at every step, from scratch.

![KV Cache: eliminating redundant K/V projections](/assets/img/llm_part4_inference/kv_cache_comparison.png)

The [TurboQuant post](https://shbhmrzd.github.io/ai/systems/ml-infrastructure/quantization/2026/04/04/turboquant-vector-quantization-for-llm-inference.html) goes deeper into KV cache compression techniques for reducing this memory bottleneck.

---

## Decoding Strategies

Once the model produces probabilities for the next token (softmax of the logits), you need to decide which token to actually use.

![Decoding strategies compared](/assets/img/llm_part4_inference/decoding_strategies.png)

### Greedy Decoding

Always pick the token with the highest probability.

```
probabilities = softmax(logits)
next_token = argmax(probabilities)
```

Suppose the model has generated "Gravity is the force that pulls objects toward" and is choosing the next token. The probabilities might look like:

```
each:       40%   → "...toward each other"
the:        30%   → "...toward the earth"
everything: 18%   → "...toward everything around it"
them:       12%   → "...toward them"
```

All four are valid continuations. Greedy always picks "each" because it has the highest probability. For the same prompt, we would always get the same output. This consistency comes at a cost as the output tends to be bland because the model always picks the safest word. It can also get stuck. After finishing a sentence, the most likely next word might start that same sentence again. So it generates it again. And again and the loop continues

### Top-k Sampling

Instead of always picking the top token, sample randomly from the top k. Sort tokens by probability, keep the top k, zero out the rest, renormalize so they sum to 1, and sample from the smaller distribution.

Using the same example with k=3:

Keep top 3: `[each: 40%, the: 30%, everything: 18%]`. Zero out "them".

Renormalize:

```
Total = 40% + 30% + 18% = 88%
each:       40% / 88% = 45.5%
the:        30% / 88% = 34.1%
everything: 18% / 88% = 20.5%
```

Now randomly sample from `[45.5%, 34.1%, 20.5%]`. Unlike greedy, which always picks the top, sampling rolls the dice weighted by these probabilities. Most of the time you get "each", but sometimes you get "the" and the response goes "toward the earth" instead. Occasionally you get "everything" and the response takes yet another direction. You never get "them" because it fell outside the top 3.

This produces more varied text than greedy and avoids the repetition loops. But k is fixed, which creates its own problem. If the model is confident about a token (say 95% probability), setting k=50 forces it to consider 49 unlikely options. If the model is uncertain and probability is spread across many tokens, k=50 might cut off reasonable choices.

### Top-p (Nucleus) Sampling

Top-p takes a different approach. Instead of keeping a fixed count, it keeps adding tokens (in order of probability) until their combined probability reaches p, usually 0.9 or 0.95. Everything below that cutoff gets dropped, the remaining probabilities are renormalized, and one token is sampled.

Using the same example with p=0.9:

Cumulative:
- each: 40%
- each + the: 70%
- each + the + everything: 88%
- each + the + everything + them: 100%

With p=0.9, we need to include tokens until cumulative probability reaches 90%. The first three tokens only sum to 88%, so we include "them" as well. All four tokens are kept.

Renormalize (they already sum to 100% in this case, so probabilities stay the same) and sample.

Now consider a different situation. The model has generated "The capital of France is" and outputs `[Paris: 97%, Lyon: 1.5%, Marseille: 1%, Berlin: 0.5%]`. With p=0.9, only "Paris" is kept (97% already exceeds 90%). When the model is confident, nucleus sampling behaves like greedy. When probability is spread, it keeps more options. This adaptiveness is why top-p is more commonly used than top-k.

### Controlling Randomness with Temperature

Temperature scales the logits before softmax ([Part 1](https://shbhmrzd.github.io/ai/ml-foundations/llm-training/2026/05/27/how-llms-process-text.html)).

```
scaled_logits = logits / temperature
probabilities = softmax(scaled_logits)
```

Temperature is often combined with top-k or top-p sampling. The full pipeline looks like this.

1. Divide logits by temperature.
2. Apply softmax to get probabilities.
3. Apply top-k or top-p filtering (keeping only the top tokens).
4. Renormalize the remaining probabilities.
5. Sample one token.

A lower temperature (say 0.3) makes the model more confident. The gap between the top token and the rest gets bigger, so when top-p runs, fewer tokens make it past the cutoff. The output becomes more focused and predictable. A higher temperature (say 1.5) does the opposite. It spreads the probability more evenly, so more tokens pass the top-p threshold and the output becomes more creative and unpredictable.

In practice, most chat applications use a temperature between 0.7 and 0.9 with top_p at 0.9. This gives enough variety to sound natural without going off the rails. For code generation, temperature is often set to 0. That turns it into greedy decoding, because correctness matters more than variety.

### Repetition Penalty and Beam Search

Temperature and top-p handle most cases, but there are two more techniques that show up regularly.

**Repetition penalty** reduces the probability of tokens that have already appeared. Without it, models tend to repeat phrases, especially in longer generations. Most inference frameworks adjust the logits downward for previously generated tokens before sampling.

**Beam search** takes a different approach. Instead of picking one token at a time and moving on, it explores multiple paths at once. Say you set beams to 3. After the first token, the model does not commit to "Gravity". It keeps three candidates: maybe "Gravity", "The", and "It". Then it extends all three. "Gravity is...", "The force...", "It pulls...". At each step it scores all the branches and drops the weakest one, always keeping the top 3. At the end, it picks the sequence with the highest total score.

The idea is that the best overall sequence might not start with the highest-probability first token. Greedy picks the best token at each step, but beam search picks the best sequence across all steps.

Beam search is widely used in machine translation. When translating "the cat sat on the mat" from English to French, there is usually one correct translation and you want the most accurate sequence, not a creative one. Beam search is good at finding that. It is less common in chat, where you want the model to sound natural and varied. Sampling with temperature and top-p works better for that.

---

## Tying It Together

1. **Tokenization**: User types "What is gravity?". The tokenizer converts it to token IDs, say `[1, 2, 3, 4]`.

2. **Prefill phase**: Run one forward pass with all 4 tokens of the prompt. The model outputs logits for the next token. The Key and Value vectors from all 32 layers and all 4 tokens are stored in the KV cache.

3. **Decode phase**: Sample a token from the logits using temperature, top-p, and other strategies. Say it is "Gravity" (token ID 5). Append it to the sequence: `[1, 2, 3, 4, 5]`.

4. **Next step**: Compute Key and Value for only token 5 (the new token). Concatenate with the cached Key and Value from tokens 1 to 4. Run attention. Output logits for token 6. Sample it.

5. **Repeat**: Generate tokens until the model outputs a stop token or the response reaches a length limit.

6. **Detokenization**: Convert the token IDs back to text. The sequence `[1, 2, 3, 4, 5, 6, 7, ...]` becomes "What is gravity? Gravity is the force..."

That is the loop that runs every time you send a prompt.

---

## Closing

When I started reading about LLMs, terms like attention, backpropagation, and KV cache kept sending me off on side research. Writing these notes forced me to connect them into a complete picture. I started with tokenization and ended up understanding why the response streams in one word at a time.

If you are a software engineer trying to make sense of all of this, I hope these four posts saved you some of the detours I took.

---

## Sources

[Vaswani et al., 2017. Attention Is All You Need](https://arxiv.org/abs/1706.03762)

[Holtzman et al., 2020. The Curious Case of Neural Text Degeneration](https://arxiv.org/abs/1904.09751)

[Touvron et al., 2023. Llama 2: Open Foundation and Fine-Tuned Chat Models](https://arxiv.org/abs/2307.09288)

[Meta, 2024. Llama 3.1 Model Card](https://github.com/meta-llama/llama-models/blob/main/models/llama3_1/MODEL_CARD.md)
