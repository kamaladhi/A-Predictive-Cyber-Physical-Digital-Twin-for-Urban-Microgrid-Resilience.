import os
import sys
import torch
import logging

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.solar.solar_forecasting import SolarLSTM, NUM_FEATURES, HORIZONS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ForecasterDiagnostic")

def diagnose_model():
    model_path = 'src/solar/models/solar_lstm.pt'
    if not os.path.exists(model_path):
        logger.error(f"Model file not found at {model_path}")
        return

    logger.info(f"Inspecting checkpoint: {model_path}")
    try:
        checkpoint = torch.load(model_path, map_location='cpu', weights_only=False)
        logger.info("Successfully loaded checkpoint file.")
        
        # Check keys
        keys = list(checkpoint.keys())
        logger.info(f"Checkpoint keys: {keys}")
        
        if 'hyperparameters' in checkpoint:
            hp = checkpoint['hyperparameters']
            logger.info(f"Hyperparameters: {hp}")
            
            # Attempt to instantiate model
            try:
                model = SolarLSTM(
                    input_size=hp.get('input_size', NUM_FEATURES),
                    hidden_size=hp.get('hidden_size', 128),
                    num_layers=hp.get('num_layers', 2),
                    dropout=hp.get('dropout', 0.3),
                    num_horizons=len(checkpoint.get('horizons', HORIZONS))
                )
                logger.info("Model class instantiated successfully.")
                
                # Attempt to load state dict
                model.load_state_dict(checkpoint['model_state_dict'])
                logger.info("State dict loaded successfully.")
                
            except Exception as e:
                logger.error(f"Model instantiation/loading failed: {e}")
                import traceback
                traceback.print_exc()
        else:
            logger.error("Missing 'hyperparameters' in checkpoint. This checkpoint may be from a legacy version.")
            
        if 'feature_scaler' in checkpoint:
            logger.info("Feature scaler found.")
        else:
            logger.error("Feature scaler missing!")

    except Exception as e:
        logger.error(f"Fatal loading error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    diagnose_model()
