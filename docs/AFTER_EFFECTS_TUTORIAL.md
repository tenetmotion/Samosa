# Samosa: After Effects quick tutorial

## Load a shot

1. Import footage and place it in a composition.
2. Select exactly one file-backed footage layer in the timeline.
3. Open **Window > Extensions (Legacy) > Samosa**.
4. Choose **Load selection**. Samosa loads the source clip and starts on the corresponding source frame.

The Samosa viewer is interactive. Use its timeline controls to move through source frames. The percentage button in the viewer corner cycles through 100%, 75%, and 50% display sizes.

## Select and track objects

1. Stay in **Object** mode and choose **Include**.
2. Click inside the subject. Add a few well-spaced points when one point is insufficient.
3. Choose **Exclude**, or right-click the viewer, to remove unwanted regions from the mask.
4. Use **+** to create another object. Each object's prompts and masks remain separate.
5. Choose **Track objects** to propagate masks through the shot.

Scrub the result. On a frame where the mask drifts, add Include or Exclude correction points and track again. **Clear tracking** removes propagated masks while retaining correction points; **Clear points** removes prompts.

## Refine the result

Use **Matting** when hair, motion blur, transparency, or soft edges need a refined alpha. Select the model and resolution, then choose **Run matting**. Alpha and Matte toggle the viewer preview.

Use **Remove** when the selected object should be filled out of the shot. OpenCV is the lighter option; MiniMax Remover is model-based and has separate noncommercial license terms.

## Export back to After Effects

1. Open **Output**.
2. Select the output description, format, and one object or **All objects**.
3. The Name field defaults to the source filename stem. Edit it only when a different base name is needed.
4. Choose an export location or leave it on the default folder.
5. Choose **Export and add to comp**.

Samosa appends the selected object and output description automatically. For example, source `interview.mov`, object `Hero Person`, and `Matting-Matte` produce `interview_Hero_Person_matting_matte`. The result is imported above the selected layer and inherits its timing and transform animation.

## Choosing an output

| Output | Use it for |
| --- | --- |
| Segmentation-Alpha | Fast hard-edged RGBA isolation |
| Segmentation-Matte | Grayscale segmentation mask |
| Segmentation-BGcolor | Subject over the configured background color |
| Matting-Alpha | Refined RGBA isolation with soft edges |
| Matting-Matte | Refined grayscale matte |
| Matting-BGcolor | Refined subject over a background color |
| ObjectRemoval | Filled plate with the selected object removed |

Model availability and commercial-use rights vary. Review [Third-party notices](../THIRD_PARTY_NOTICES.md) before selecting a production engine.
