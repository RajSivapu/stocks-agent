import { committedDeliveryUncertainText } from "./webhook-utils.mjs";

export async function acknowledgeCommittedCommand({ telegram, sendText, callback, result, resultText }) {
  const committed = result?.ok === true;
  try {
    await telegram("answerCallbackQuery", {
      callback_query_id: callback.id,
      text: committed ? "Recorded." : "Nothing changed.",
    });
    await telegram("editMessageText", {
      chat_id: callback.message.chat.id,
      message_id: callback.message.message_id,
      text: resultText,
      reply_markup: { inline_keyboard: [] },
    });
    return { committed, acknowledgement: "delivered" };
  } catch (error) {
    if (!committed) throw error;
    try {
      await sendText(callback.message.chat.id, committedDeliveryUncertainText(resultText));
    } catch {
      // The command is already committed. A failed fallback cannot turn it into a rollback.
    }
    return { committed: true, acknowledgement: "uncertain" };
  }
}
