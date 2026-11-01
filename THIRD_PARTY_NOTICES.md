# Third-party notices

PactTrail is licensed under Apache-2.0. Third-party packages retain their own copyrights and licenses.

| Component | Version / source | License | Use |
|---|---|---|---|
| [LightAgent](https://github.com/wanxingai/LightAgent/tree/v0.10.0) | 0.10.0 | Apache-2.0 | Compatibility adapter; the main agent uses PactTrail's serial tool loop. |
| [DeepSeek tokenizer data](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/tree/df42c109f1defefcbfcedbe7d905718a12266e40) | df42c109f1defefcbfcedbe7d905718a12266e40 | MIT | Bundled tokenizer data and restricted text rendering; no model weights. |
| [Tokenizers](https://github.com/huggingface/tokenizers/tree/v0.23.2) | 0.23.2 | Apache-2.0 | Local token estimation; no Hub download is used. |
| [Loguru](https://github.com/Delgan/loguru/tree/0.7.3) | 0.7.3 | MIT | Transitive Python dependency in the Windows offline bundle. |

The original license texts are preserved in `licenses/`. LightAgent's license includes its upstream application notice; it is reproduced unchanged. Other dependency license files remain inside the original dependency wheels. The Windows offline bundle also contains `dependencies/DEPENDENCIES.json`, listing all bundled distributions, exact wheel hashes and license metadata. Installing the standalone PactTrail wheel resolves dependencies separately.

第三方组件保留各自版权与许可证。Windows 离线包提供逐项依赖清单及 SHA-256；第三方许可原文和署名保持不变。
