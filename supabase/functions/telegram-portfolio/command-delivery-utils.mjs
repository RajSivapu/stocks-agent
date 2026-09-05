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

export function isDefinitiveServerRejection(error) {
  if (!error || typeof error !== "object") return false;
  const { code, status } = error;
  if (typeof code === "string" && (/^[0-9A-Z]{5}$/.test(code) || /^PGRST\d{3}$/.test(code))) {
    return true;
  }
  return Number.isInteger(status) && status >= 400 && status < 500;
}

function uncertainRpcOutcomeText(receipt, resultText, definitiveRejection) {
  const renderedResult = typeof resultText === "function"
    ? resultText(receipt?.result ?? null)
    : resultText;
  if (receipt?.result?.ok === true) {
    return `${renderedResult}\nTelegram acknowledgement is uncertain; reconciliation is required before retrying.`;
  }
  if (receipt?.result?.ok === false) return "No change was recorded.";
  if (definitiveRejection) return "No change was recorded.";
  return "Command outcome is uncertain; reconciliation is required before retrying.";
}

export async function reconcileLostCommandAcknowledgementRpc({
  readReceipt,
  telegram,
  callback,
  resultText,
  definitiveRejection = false,
}) {
  let receipt = null;
  try {
    receipt = await readReceipt();
  } catch {
    // A receipt read can fail for the same transport reason as the original RPC.
  }
  try {
    await telegram("answerCallbackQuery", {
      callback_query_id: callback.id,
      text: uncertainRpcOutcomeText(receipt, resultText, definitiveRejection),
      show_alert: true,
    });
  } catch {
    // Do not manufacture a rollback claim if Telegram cannot display the notice.
  }
  return receipt;
}
