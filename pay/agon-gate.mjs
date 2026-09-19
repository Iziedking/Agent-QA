/**
 * Keep invalid paid requests from reaching Circle settlement while allowing an
 * unsigned discovery request to receive the standard x402 challenge first.
 */
export function createAgonPaymentGate({ requirePayment, validateBody }) {
  if (typeof requirePayment !== "function") {
    return (_req, res) => {
      res.status(503).json({ error: "Arc Testnet x402 is not enabled for this provider" });
    };
  }

  return (req, res, next) => {
    const paymentSignature = req.headers?.["payment-signature"];
    if (typeof paymentSignature === "string" && paymentSignature.length > 0) {
      const validationError = validateBody(req.body ?? {});
      if (validationError) {
        res.status(400).json(validationError);
        return;
      }
    }

    try {
      Promise.resolve(requirePayment(req, res, next)).catch(next);
    } catch (error) {
      next(error);
    }
  };
}
